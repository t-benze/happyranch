/** Mirror of runtime/daemon/routes/custom_skills.py — founder Custom Skills UI. */
import { request } from './client';

export type EligibilityRule = { scope_type: 'agent' | 'team' | 'org'; scope_target?: string | null; effect: 'allow' | 'deny' };

export interface CustomSkill {
  id?: string;
  skill_id?: string;
  slug: string;
  name: string;
  description: string;
  current_version_id: number;
  retired_at: string | null;
  retired_reason?: string | null;
  validation_state: string;
  content_hash?: string;
  hidden_reason?: string | null;
}

export interface CustomSkillVersion {
  id: number;
  content_hash: string;
  skill_md_cache?: string;
  created_at: string;
  author_kind?: string;
  author_identity?: string;
  validation_state?: string;
}

export type CustomSkillInput = { slug: string; name: string; description?: string; skill_md: string };
export type CustomSkillCreateResult = { skill_id: string; version_id: number; content_hash: string; validation_state: string; hidden_reason?: string };
export type EligibilityImpact = { newly_visible: string[]; newly_hidden: string[]; unchanged: string[]; revision: number };

export const customSkillId = (skill: Pick<CustomSkill, 'id' | 'skill_id'>): string =>
  skill.skill_id ?? skill.id ?? '';

export const listCustomSkills = (slug: string, filter?: string): Promise<{ skills: CustomSkill[] }> =>
  request(`/orgs/${slug}/custom-skills/catalog`, { params: { filter } });

export const createCustomSkill = (slug: string, body: CustomSkillInput): Promise<CustomSkillCreateResult> =>
  request(`/orgs/${slug}/custom-skills`, { method: 'POST', body });

export const getCustomSkill = (slug: string, skillId: string): Promise<CustomSkill> =>
  request(`/orgs/${slug}/custom-skills/${encodeURIComponent(skillId)}`);

export const patchCustomSkill = (slug: string, skillId: string, body: Pick<CustomSkillInput, 'name' | 'description'>): Promise<CustomSkill> =>
  request(`/orgs/${slug}/custom-skills/${encodeURIComponent(skillId)}`, { method: 'PATCH', body });

export const listCustomSkillVersions = (slug: string, skillId: string): Promise<{ versions: CustomSkillVersion[] }> =>
  request(`/orgs/${slug}/custom-skills/${encodeURIComponent(skillId)}/versions`);

export const getCustomSkillVersionDiff = (slug: string, skillId: string, a: number, b: number): Promise<{ a: CustomSkillVersion; b: CustomSkillVersion; diff: string[] }> =>
  request(`/orgs/${slug}/custom-skills/${encodeURIComponent(skillId)}/versions/${a}/diff/${b}`);

export const addCustomSkillVersion = (slug: string, skillId: string, body: Pick<CustomSkillInput, 'skill_md'>): Promise<CustomSkillCreateResult> =>
  request(`/orgs/${slug}/custom-skills/${encodeURIComponent(skillId)}/versions`, { method: 'POST', body });

export const retireCustomSkill = (slug: string, skillId: string, body?: { reason?: string }): Promise<CustomSkill> =>
  request(`/orgs/${slug}/custom-skills/${encodeURIComponent(skillId)}/retire`, { method: 'POST', body });

export const restoreCustomSkill = (slug: string, skillId: string): Promise<CustomSkill> =>
  request(`/orgs/${slug}/custom-skills/${encodeURIComponent(skillId)}/restore`, { method: 'POST' });

export const getCustomSkillEligibility = (slug: string, skillId: string): Promise<{ rules: EligibilityRule[]; revision: number }> =>
  request(`/orgs/${slug}/custom-skills/${encodeURIComponent(skillId)}/eligibility`);

export const previewCustomSkillEligibility = (slug: string, skillId: string, rules: EligibilityRule[]): Promise<EligibilityImpact> =>
  request(`/orgs/${slug}/custom-skills/${encodeURIComponent(skillId)}/eligibility/preview`, { method: 'POST', body: rules });

export const saveCustomSkillEligibility = (slug: string, skillId: string, rules: EligibilityRule[], revision: number): Promise<EligibilityImpact> =>
  request(`/orgs/${slug}/custom-skills/${encodeURIComponent(skillId)}/eligibility`, { method: 'PUT', body: rules, headers: { 'If-Match': String(revision) } });

export const explainCustomSkillEligibility = (slug: string, skillId: string, agent: string): Promise<{ visible: boolean; hidden_reason: string | null; winning_rule: EligibilityRule | null }> =>
  request(`/orgs/${slug}/custom-skills/${encodeURIComponent(skillId)}/eligibility/explain`, { params: { agent } });

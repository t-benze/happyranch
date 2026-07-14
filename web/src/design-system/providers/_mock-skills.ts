/**
 * Mock implementation of `SkillsApi` for the prototype sandbox + the FE
 * screenshot harness (THR-092 Slice 1). The backend is merged on main but not
 * yet deployed to the live daemon, so catalog fidelity is proven against
 * these fixtures.
 *
 * Coverage (per brief): read-only bundled system contracts, managed bundled
 * skills, and user-authored custom skills across all three validation_state
 * values (in_catalog / validated / failed_validation), including at least one
 * has_assigned_not_yet_effective row (takes effect next session).
 *
 * The Bundled / Custom filter is applied here to mirror the daemon's
 * `?filter=` bucketing: Bundled = managed + system_contract, Custom =
 * user_authored.
 */
import type { CatalogSkillItem } from '@/lib/api/skills';
import type { QueryLike, SkillsApi } from './DataContext';

function ok<T>(data: T): QueryLike<T> {
  return { data, isLoading: false, isError: false, error: null };
}

const FIXTURES: CatalogSkillItem[] = [
  {
    skill_id: 'sk-founder-escalation-protocol',
    name: 'founder-escalation-protocol',
    type: 'system_contract',
    source: 'bundled',
    system_contract: true,
    visibility_category: 'read_only',
    policy_class: 'contract',
    status: 'active',
    version: 'locked',
    validation_state: 'validated',
    assigned_agent_count: 5,
    effective_agent_count: 5,
    has_assigned_not_yet_effective: false,
    summary:
      'Escalate merges to main, protocol changes, and genuine ambiguity to the founder. Everything else proceeds autonomously.',
  },
  {
    skill_id: 'sk-append-only-audit',
    name: 'append-only-audit',
    type: 'system_contract',
    source: 'bundled',
    system_contract: true,
    visibility_category: 'read_only',
    policy_class: 'contract',
    status: 'active',
    version: 'locked',
    validation_state: 'validated',
    assigned_agent_count: 5,
    effective_agent_count: 5,
    has_assigned_not_yet_effective: false,
    summary:
      'Never rewrite history. Every state change is an append-only event other agents can replay.',
  },
  {
    skill_id: 'sk-kb-curation',
    name: 'kb-curation',
    type: 'managed',
    source: 'bundled',
    system_contract: false,
    visibility_category: 'toggleable',
    policy_class: 'guidance',
    status: 'active',
    version: '2.1.0',
    validation_state: 'validated',
    assigned_agent_count: 4,
    effective_agent_count: 3,
    has_assigned_not_yet_effective: true,
    summary:
      'How to search, write, and promote durable cross-agent knowledge without duplicating task-specific notes.',
  },
  {
    skill_id: 'sk-web-fidelity-loop',
    name: 'web-fidelity-loop',
    type: 'managed',
    source: 'bundled',
    system_contract: false,
    visibility_category: 'toggleable',
    policy_class: 'guidance',
    status: 'active',
    version: '1.4.0',
    validation_state: 'validated',
    assigned_agent_count: 2,
    effective_agent_count: 2,
    has_assigned_not_yet_effective: false,
    summary:
      'Build, screenshot, diff, and fix a UI against its design target before handing off to review.',
  },
  {
    skill_id: 'sk-tourism-partner-playbook',
    name: 'tourism-partner-playbook',
    type: 'user_authored',
    source: 'custom',
    system_contract: false,
    visibility_category: 'toggleable',
    policy_class: 'guidance',
    status: 'active',
    version: '1.2.0',
    validation_state: 'validated',
    assigned_agent_count: 3,
    effective_agent_count: 3,
    has_assigned_not_yet_effective: false,
    summary:
      'House style for briefing partner venues on itineraries, seasonal windows, and cancellation handling.',
  },
  {
    skill_id: 'sk-refund-decision-guide',
    name: 'refund-decision-guide',
    type: 'user_authored',
    source: 'custom',
    system_contract: false,
    visibility_category: 'toggleable',
    policy_class: 'guidance',
    status: 'draft',
    version: '0.1.0',
    validation_state: 'in_catalog',
    assigned_agent_count: 0,
    effective_agent_count: 0,
    has_assigned_not_yet_effective: false,
    summary:
      'Draft guidance for when to offer a full versus partial refund. Saved but not yet validated.',
  },
  {
    skill_id: 'sk-vendor-comms-style',
    name: 'vendor-comms-style',
    type: 'user_authored',
    source: 'custom',
    system_contract: false,
    visibility_category: 'toggleable',
    policy_class: 'guidance',
    status: 'draft',
    version: '0.3.0',
    validation_state: 'failed_validation',
    assigned_agent_count: 0,
    effective_agent_count: 0,
    has_assigned_not_yet_effective: false,
    summary:
      'Tone and escalation rules for vendor emails. Last validation failed — SKILL.md is missing a required version field.',
  },
];

function bucket(item: CatalogSkillItem): 'Bundled' | 'Custom' {
  return item.type === 'user_authored' ? 'Custom' : 'Bundled';
}

export const mockSkillsApi: SkillsApi = {
  useSkillsCatalog: (params) => {
    const filter = params?.filter;
    const items = filter
      ? FIXTURES.filter((i) => bucket(i) === filter)
      : FIXTURES;
    return ok({ items });
  },
};

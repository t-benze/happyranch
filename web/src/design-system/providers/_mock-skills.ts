/**
 * Mock implementation of `SkillsApi` for the prototype sandbox + the FE
 * screenshot harness (THR-092 Slices 1–2). The backend is merged on main but
 * not yet deployed to the live daemon, so fidelity is proven against these
 * fixtures.
 *
 * Catalog coverage (Slice 1): read-only bundled system contracts, managed
 * bundled skills, and user-authored custom skills across all three
 * validation_state values (in_catalog / validated / failed_validation),
 * including at least one has_assigned_not_yet_effective row.
 *
 * Detail coverage (Slice 2, `DETAILS`): source (bundled/SKILL.md content),
 * source-gating (read-only system contract, read-only managed bundled,
 * editable custom), a failed-validation "needs attention" custom draft, and
 * per-agent assignments spanning effective / assigned-not-yet-effective /
 * not-assigned so the provenance vocabulary is fully exercised.
 *
 * The Bundled / Custom filter is applied here to mirror the daemon's
 * `?filter=` bucketing: Bundled = managed + system_contract, Custom =
 * user_authored.
 */
import type {
  AssignSkillRequest,
  AssignSkillResponse,
  CatalogSkillItem,
  CreateSkillRequest,
  CreateSkillResponse,
  EditSkillRequest,
  EditSkillResponse,
  SkillDetail,
  SkillStatusResponse,
  ValidateSkillResponse,
} from '@/lib/api/skills';
import type { MutationLike, QueryLike, SkillsApi } from './DataContext';

function ok<T>(data: T): QueryLike<T> {
  return { data, isLoading: false, isError: false, error: null };
}

function notFound<T>(): QueryLike<T> {
  return {
    data: undefined,
    isLoading: false,
    isError: true,
    error: new Error('skill not found'),
  } as QueryLike<T>;
}

/** Non-pending mutation whose `mutateAsync` resolves to `resolve(args)`. Used
 *  for the Slice-3 create/validate fixtures so the prototype + screenshot
 *  harness can exercise both the pass and the fail result without a daemon. */
function mockMutation<TArgs, TResult>(
  resolve: (args: TArgs) => TResult,
): MutationLike<TArgs, TResult> {
  return {
    isPending: false,
    mutateAsync: (args: TArgs) => Promise.resolve(resolve(args)),
  };
}

// Slice-3 create/validate fixtures. A slug that opts into the failure path
// (contains `fail`, or collides with a bundled slug) returns the
// failed-validation draft; everything else validates. This lets the prototype
// and the screenshot harness render BOTH the success and the failure result
// (spec v3 §9.1: a failure still persists an editable draft).
const BUNDLED_SLUGS = new Set(['kb-curation', 'web-fidelity-loop', 'jobs']);

function wantsFailure(slug: string): boolean {
  const s = slug.trim().toLowerCase();
  return s.includes('fail') || BUNDLED_SLUGS.has(s);
}

function mockCreateResponse(body: CreateSkillRequest): CreateSkillResponse {
  const skillId = `hr:${body.slug.trim() || 'draft'}`;
  if (wantsFailure(body.slug)) {
    return {
      skill_id: skillId,
      source: 'user_authored',
      validation_state: 'in_catalog',
      validation: {
        ok: false,
        errors: [
          "slug collides with release skill 'kb-curation'",
          'The references/pricing.md asset could not be resolved.',
        ],
      },
    };
  }
  return {
    skill_id: skillId,
    source: 'user_authored',
    validation_state: 'validated',
    validation: { ok: true, errors: [] },
  };
}

// Slice-4 edit fixture. A body whose name/skill_md opts into the failure path
// (contains `fail`) returns the failed-validation draft; everything else
// validates. The response echoes the submitted version so a bumped version
// drives the edited-effective (takes-effect-next-session) result state
// (spec v3 §9.5). A failure still persists an editable draft (§9.1a).
function mockEditResponse(
  skillId: string,
  body: EditSkillRequest,
): EditSkillResponse {
  const version = (body.version ?? '').trim() || '0.0.0';
  const optsFail = `${body.name ?? ''} ${body.skill_md ?? ''}`
    .toLowerCase()
    .includes('fail');
  if (optsFail) {
    return {
      skill_id: skillId,
      source: 'user_authored',
      validation_state: 'in_catalog',
      validation: {
        ok: false,
        errors: [
          'SKILL.md is missing a required version field.',
          'The references/pricing.md asset could not be resolved.',
        ],
      },
      version,
    };
  }
  return {
    skill_id: skillId,
    source: 'user_authored',
    validation_state: 'validated',
    validation: { ok: true, errors: [] },
    version,
  };
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

// ── Slice-2 single-skill detail fixtures ────────────────────────────────
// Keyed by skill_id. Each carries the SKILL.md-derived content (description /
// when_to_use), source, validation, and — for assignable skills — a per-agent
// assignments[] spanning the full provenance vocabulary.
const DETAILS: Record<string, SkillDetail> = {
  // Read-only system contract: applied by context predicate, NOT per-agent →
  // no assignments[]; the detail shows the predicate rollup + a lock.
  'sk-founder-escalation-protocol': {
    skill_id: 'sk-founder-escalation-protocol',
    name: 'founder-escalation-protocol',
    type: 'system_contract',
    source: 'bundled · contracts/founder-escalation-protocol/SKILL.md',
    system_contract: true,
    visibility_category: 'read_only',
    policy_class: 'contract',
    status: 'enabled',
    version: 'locked',
    validation_state: 'validated',
    summary:
      'Escalate merges to main, protocol changes, and genuine ambiguity to the founder. Everything else proceeds autonomously.',
    description:
      'A system contract that every agent is shown by context. It defines when to hand a decision back to the founder rather than proceed alone.',
    when_to_use:
      'Before merging to main, changing protocol surfaces, or acting under genuine ambiguity.',
    owner: 'platform',
    validation: { ok: true, errors: [] },
  },
  // Read-only managed bundled skill, but assignable → per-agent assignments[]
  // with an effective agent, an assigned-not-yet-effective agent, and an
  // unassigned agent. No edit entry point (platform-managed).
  'sk-kb-curation': {
    skill_id: 'sk-kb-curation',
    name: 'kb-curation',
    type: 'managed',
    source: 'bundled · skills/kb-curation/SKILL.md',
    system_contract: false,
    visibility_category: 'toggleable',
    policy_class: 'guidance',
    status: 'enabled',
    version: '2.1.0',
    validation_state: 'validated',
    summary:
      'How to search, write, and promote durable cross-agent knowledge without duplicating task-specific notes.',
    description:
      'Guidance for curating the shared knowledge base: when to add an entry, how to phrase it for reuse, and when to promote a learning.',
    when_to_use:
      'When a task uncovers a durable, cross-agent fact worth preserving beyond the current work.',
    owner: 'platform',
    validation: { ok: true, errors: [] },
    assignments: [
      { agent: 'kb_curator', assigned: true, effective: true, state: 'effective' },
      { agent: 'research_lead', assigned: true, effective: true, state: 'effective' },
      { agent: 'ops_agent', assigned: true, effective: true, state: 'effective' },
      {
        agent: 'support_agent',
        assigned: true,
        effective: false,
        state: 'assigned_not_yet_effective',
      },
      { agent: 'sales_agent', assigned: false, effective: false, state: 'effective' },
    ],
  },
  // Editable custom skill (validated) → Edit entry point + per-agent table.
  'sk-tourism-partner-playbook': {
    skill_id: 'sk-tourism-partner-playbook',
    name: 'tourism-partner-playbook',
    type: 'user_authored',
    source: 'custom · store/tourism-partner-playbook/SKILL.md',
    system_contract: false,
    visibility_category: 'toggleable',
    policy_class: 'guidance',
    status: 'enabled',
    version: '1.2.0',
    validation_state: 'validated',
    summary:
      'House style for briefing partner venues on itineraries, seasonal windows, and cancellation handling.',
    description:
      'Your team’s house style for partner-venue briefings: how to frame itineraries, flag seasonal windows, and phrase cancellation terms consistently.',
    when_to_use:
      'When drafting or reviewing any outbound brief to a partner venue.',
    owner: 'operator',
    validation: { ok: true, errors: [] },
    assignments: [
      { agent: 'partner_liaison', assigned: true, effective: true, state: 'effective' },
      { agent: 'itinerary_planner', assigned: true, effective: true, state: 'effective' },
      {
        agent: 'support_agent',
        assigned: true,
        effective: false,
        state: 'assigned_not_yet_effective',
      },
    ],
  },
  // Editable custom skill that FAILED validation → "needs attention" banner
  // with plain-language issues; no agents assigned yet (can't assign until it
  // validates). Draft is preserved and editable.
  'sk-vendor-comms-style': {
    skill_id: 'sk-vendor-comms-style',
    name: 'vendor-comms-style',
    type: 'user_authored',
    source: 'custom · store/vendor-comms-style/SKILL.md',
    system_contract: false,
    visibility_category: 'toggleable',
    policy_class: 'guidance',
    status: 'draft',
    version: '0.3.0',
    validation_state: 'failed_validation',
    summary:
      'Tone and escalation rules for vendor emails. Last validation failed — SKILL.md is missing a required version field.',
    description:
      'Draft guidance for vendor email tone and when to escalate a thread. Saved as an editable draft — nothing is lost while you fix it.',
    when_to_use:
      'When writing to a vendor and deciding whether a reply needs escalation.',
    owner: 'operator',
    validation: {
      ok: false,
      errors: [
        'SKILL.md is missing a required version field.',
        'The references/pricing.md asset could not be resolved.',
      ],
    },
    assignments: [],
  },
};

// ── Slice-5 per-agent assignment status fixtures ────────────────────────
// Keyed by skill_id. MATCHES PRODUCTION: the daemon's status endpoint returns
// ONLY already-assigned agents (it skips unassigned agents), so these fixtures
// carry assigned rows only. The full candidate roster (which surfaces the
// unassigned, assignable agents) is derived by the panel from the real agents
// source and unioned with this response — it is NOT seeded here. Coverage:
// effective agents + an assigned-not-yet-effective agent.
const STATUS: Record<string, SkillStatusResponse> = {
  'sk-tourism-partner-playbook': {
    skill_id: 'sk-tourism-partner-playbook',
    source: 'user_authored',
    in_catalog: true,
    validated: true,
    current_version: '1.2.0',
    assignments: [
      {
        agent: 'partner_liaison',
        assigned: true,
        effective: true,
        materialized_version: '1.2.0',
        state: 'effective',
      },
      {
        agent: 'itinerary_planner',
        assigned: true,
        effective: true,
        materialized_version: '1.2.0',
        state: 'effective',
      },
      {
        agent: 'support_agent',
        assigned: true,
        effective: false,
        materialized_version: '1.1.0',
        state: 'assigned_not_yet_effective',
      },
    ],
    last_validation: { ok: true, version: '1.2.0', at: '2026-07-14T10:00:00Z' },
  },
  // A failed-validation custom draft: not shown to any agent yet and not yet
  // assignable — the panel renders the read-only "resolve validation first"
  // state. Production returns no assigned agents for it.
  'sk-vendor-comms-style': {
    skill_id: 'sk-vendor-comms-style',
    source: 'user_authored',
    in_catalog: true,
    validated: false,
    current_version: '0.3.0',
    assignments: [],
    last_validation: { ok: false, version: null, at: '2026-07-14T09:00:00Z' },
  },
};

// The post-commit success returned by the assign endpoint. `state` mirrors the
// requested action; `materializes_on` communicates the next-session semantics
// in transport terms (the UI renders its own guidance-visibility copy).
function mockAssignResponse(
  agentId: string,
  skillId: string,
  body: AssignSkillRequest,
): AssignSkillResponse {
  const assigning = body.action === 'allow';
  return {
    agent_id: agentId,
    skill_id: skillId,
    state: assigning ? 'assigned' : 'unassigned',
    effective_hint: assigning ? 'assigned_not_yet_effective' : null,
    materializes_on: assigning ? 'next_session' : null,
  };
}

export const mockSkillsApi: SkillsApi = {
  useSkillsCatalog: (params) => {
    const filter = params?.filter;
    const items = filter
      ? FIXTURES.filter((i) => bucket(i) === filter)
      : FIXTURES;
    return ok({ items });
  },
  useSkillDetail: (skillId) => {
    if (!skillId) return notFound<SkillDetail>();
    const detail = DETAILS[skillId];
    return detail ? ok(detail) : notFound<SkillDetail>();
  },
  useCreateSkill: () =>
    mockMutation<CreateSkillRequest, CreateSkillResponse>(mockCreateResponse),
  useValidateSkill: () =>
    mockMutation<{ skillId: string }, ValidateSkillResponse>(({ skillId }) => {
      // Re-validation mirrors create: a slug embedded in the id opts into the
      // failure path, otherwise it validates clean.
      const failed = wantsFailure(skillId);
      return {
        skill_id: skillId,
        validation_state: failed ? 'in_catalog' : 'validated',
        validation: failed
          ? { ok: false, errors: ['The references/pricing.md asset could not be resolved.'] }
          : { ok: true, errors: [] },
      };
    }),
  useEditSkill: () =>
    mockMutation<{ skillId: string; body: EditSkillRequest }, EditSkillResponse>(
      ({ skillId, body }) => mockEditResponse(skillId, body),
    ),
  useSkillStatus: (skillId) => {
    if (!skillId) return notFound<SkillStatusResponse>();
    const status = STATUS[skillId];
    return status ? ok(status) : notFound<SkillStatusResponse>();
  },
  useAssignSkill: () =>
    mockMutation<
      { agentId: string; skillId: string; body: AssignSkillRequest },
      AssignSkillResponse
    >(({ agentId, skillId, body }) =>
      mockAssignResponse(agentId, skillId, body),
    ),
};

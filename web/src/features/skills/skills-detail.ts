/**
 * Pure, provider-agnostic helpers for the Skill Detail + per-agent
 * effective/provenance surface (THR-092 Slice 2 of 6). Source-gating and the
 * provenance product-language mappers live here, apart from the JSX, so the
 * copy discipline is unit-tested:
 *
 *   - NO user-facing "active"; assigned-but-not-yet-effective always reads
 *     "Takes effect next session" (spec §3.1, §2).
 *   - NO permission / approve / admit / materialize-now language. Provenance
 *     describes GUIDANCE VISIBILITY only — never a tool/command grant
 *     (spec §2 copy-discipline invariant).
 *   - Source-gating: only user-authored (custom) skills are editable; bundled
 *     (managed) and system-contract skills are read-only here (brief:
 *     "system_contract / bundled rows are READ-ONLY … custom … show an EDIT
 *     entry point").
 *
 * The functions take minimal STRUCTURAL inputs (not the `@/lib/api/skills`
 * types) so this pure module stays decoupled from the data layer and clear of
 * the features/* no-restricted-imports rule (MEM-032).
 */
import type { MessageKey } from '@/lib/i18n';
import type { ValidationTone } from './skills-catalog';

/** Minimal structural shape of a skill's source/kind facts. */
export interface SkillSourceFacts {
  type: string; // 'managed' | 'system_contract' | 'user_authored'
  system_contract: boolean;
}

export type SkillSource = 'bundled' | 'custom';

/** Custom (user-authored) skills come from the per-org writable store; every
 *  other source (managed catalog + system contracts) is bundled/platform. */
export function skillSource(facts: SkillSourceFacts): SkillSource {
  return facts.type === 'user_authored' ? 'custom' : 'bundled';
}

/** Only user-authored custom skills are editable by the operator. Bundled and
 *  system-contract skills are platform-managed → read-only on the detail
 *  surface (no edit / re-validate entry point). A user-authored skill can
 *  never mint a system_contract (spec §3.4), but the guard is explicit. */
export function isEditableSkill(facts: SkillSourceFacts): boolean {
  return facts.type === 'user_authored' && !facts.system_contract;
}

export function isReadOnlySkill(facts: SkillSourceFacts): boolean {
  return !isEditableSkill(facts);
}

/**
 * Read-only rationale in product language — source-specific so a managed
 * bundled skill (assignable via the Slice-3 config screen) is NOT told it
 * "cannot be unassigned", while a system contract (applied by context
 * predicate, never per-agent) is. Returns `null` for editable custom skills.
 */
export function readOnlyReason(facts: SkillSourceFacts): MessageKey | null {
  if (isEditableSkill(facts)) return null;
  if (facts.system_contract) return 'skills.detail.readOnlyContract';
  return 'skills.detail.readOnlyBundled';
}

/** Minimal structural shape of the skill-level validation facts. */
export interface SkillValidationFacts {
  validation_state: string; // 'in_catalog' | 'validated' | 'failed_validation'
  validation?: { ok: boolean; errors?: string[] } | null;
}

/** A skill "needs attention" when its last technical validation failed — a
 *  fixable draft, NOT an approval/trust gate (spec §3.1). Reads either the
 *  skill-level `validation_state` or the richer `validation.ok` when present. */
export function needsAttention(facts: SkillValidationFacts): boolean {
  if (facts.validation && facts.validation.ok === false) return true;
  return facts.validation_state === 'failed_validation';
}

/** Plain-language explanation lines for a failed validation ("what needs
 *  fixing", handoff §3). Empty when the skill is not in a failed state. */
export function validationIssues(facts: SkillValidationFacts): string[] {
  if (!needsAttention(facts)) return [];
  return facts.validation?.errors?.filter((e) => e.trim().length > 0) ?? [];
}

// ── Per-agent effective / provenance ───────────────────────────────────

/** Minimal structural shape of one per-(skill,agent) assignment row, matching
 *  `SkillDetail.assignments[]` / `SkillStatusResponse.assignments[]`. */
export interface AgentAssignmentFacts {
  agent: string;
  assigned: boolean;
  effective: boolean;
  state?: string; // 'effective' | 'assigned_not_yet_effective'
}

export type AgentEffectiveStatus =
  | 'effective'
  | 'not_yet_effective'
  | 'not_assigned';

export interface AgentProvenance {
  agent: string;
  status: AgentEffectiveStatus;
  /** Catalog key for the short state chip label — never "active". */
  statusLabel: MessageKey;
  tone: ValidationTone;
  /** True only for assigned-but-not-yet-effective — drives the
   *  "takes effect next session" indicator. */
  takesEffectNextSession: boolean;
  /** One-sentence "why this skill is / isn't effective for this agent",
   *  in guidance-visibility language (never permission wording) — a catalog key. */
  reason: MessageKey;
}

function isEffective(a: AgentAssignmentFacts): boolean {
  return a.assigned && (a.effective || a.state === 'effective');
}

/**
 * Derive one agent's effective status + a product-language provenance reason
 * from the raw assignment facts. Three honest outcomes:
 *
 *   - effective          — the current store version has materialized on disk
 *                          and is shown to the agent (§7.1).
 *   - not_yet_effective  — assigned, but the current version isn't materialized
 *                          yet → "Takes effect next session" (§2).
 *   - not_assigned       — no eligibility rule → this skill is not shown to the
 *                          agent as guidance.
 *
 * Never emits "active" and never implies a permission/tool grant.
 */
export function agentProvenance(a: AgentAssignmentFacts): AgentProvenance {
  if (!a.assigned) {
    return {
      agent: a.agent,
      status: 'not_assigned',
      statusLabel: 'skills.provenance.notAssigned',
      tone: 'neutral',
      takesEffectNextSession: false,
      reason: 'skills.provenance.reason.notAssigned',
    };
  }
  if (isEffective(a)) {
    return {
      agent: a.agent,
      status: 'effective',
      statusLabel: 'skills.provenance.effective',
      tone: 'positive',
      takesEffectNextSession: false,
      reason: 'skills.provenance.reason.effective',
    };
  }
  return {
    agent: a.agent,
    status: 'not_yet_effective',
    statusLabel: 'skills.provenance.takesEffect',
    tone: 'attention',
    takesEffectNextSession: true,
    reason: 'skills.provenance.reason.notYetEffective',
  };
}

export function agentProvenanceList(
  assignments: AgentAssignmentFacts[],
): AgentProvenance[] {
  return assignments.map(agentProvenance);
}

export interface AssignmentRollup {
  assigned: number;
  effective: number;
  notYetEffective: number;
}

/** Rollup counts across the per-agent assignments — mirrors the catalog
 *  rollups (§1.1) but computed from the detail's own `assignments[]`. */
export function assignmentRollup(
  assignments: AgentAssignmentFacts[],
): AssignmentRollup {
  let assigned = 0;
  let effective = 0;
  for (const a of assignments) {
    if (a.assigned) assigned += 1;
    if (isEffective(a)) effective += 1;
  }
  return { assigned, effective, notYetEffective: assigned - effective };
}

/** Destination for the B2 custom-skill edit surface. */
export function editRoutePath(slug: string, skillId: string): string {
  return `/orgs/${slug}/skills/custom/${skillId}`;
}

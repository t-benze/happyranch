/**
 * Pure, provider-agnostic helpers for the Edit + Re-validate a custom skill
 * surface (THR-092 Slice 4 of 6). Everything that must obey the copy discipline
 * or the edit-safety rules lives HERE, apart from the JSX, so it is unit-tested:
 *
 *   - A custom skill is user_authored ONLY. This module never emits a
 *     `policy_class`, so an edit can never mint or alter a system_contract
 *     (spec v3 §3.4); `EditSkillRequest` has no policy_class field at all.
 *   - PREFILL-SAFE EDIT (matches the daemon PATCH merge, spec v3 §9.5): the
 *     detail fetch (getSkillCatalogDetail) exposes name / summary / version but
 *     NOT the raw SKILL.md body or the references/asset maps. The daemon PATCH
 *     PRESERVES the stored SKILL.md when `skill_md` is omitted, so an untouched
 *     SKILL.md field is left blank and simply not sent — the current guidance is
 *     kept, never blanked. `references`/`assets` are full-REPLACEMENT maps on
 *     edit (the daemon resets them when the field is present), so they are only
 *     sent when the operator provides at least one file.
 *   - DRAFT-PERSIST ON FAILURE (spec v3 §9.1a / §9.5): a failed re-validation
 *     still persists an editable draft — nothing is lost. The result copy frames
 *     failure as a fixable technical check, never a rejection.
 *   - EDITED-EFFECTIVE MODEL (spec v3 §7.1 / §2): a successful edit that bumps
 *     the version makes any already-effective agent "assigned, not yet
 *     effective" — the new version takes effect at that agent's next session.
 *     The provenance vocabulary is REUSED from `skills-detail` (never a parallel
 *     copy), so this module only re-shapes the assignment facts.
 *
 * Inputs are minimal STRUCTURAL shapes (not the `@/lib/api/skills` types) so
 * this module stays decoupled from the data layer and clear of the features/*
 * no-restricted-imports rule (MEM-032). The validation-result mappers and the
 * validation-check explanation are REUSED from `skills-create`, not re-derived.
 */
import type { NamedFileEntry } from './skills-create';
import type { AgentAssignmentFacts } from './skills-detail';

export type { NamedFileEntry } from './skills-create';

// ── Form → request ──────────────────────────────────────────────────────

/** Raw form state for the edit surface. There is no `slug` — a skill's id is
 *  immutable; only its content and metadata are editable. All strings pre-trim. */
export interface EditSkillFormValues {
  name: string;
  /** Optional (EditSkillRequest.version?). */
  version: string;
  /** Optional (EditSkillRequest.summary?). */
  summary: string;
  /** The SKILL.md body. BLANK = keep the current stored guidance (the detail
   *  fetch does not expose the raw body, and the daemon preserves it when this
   *  field is omitted). Non-blank = replace the guidance. */
  skillMd: string;
  references: NamedFileEntry[];
  assets: NamedFileEntry[];
}

/** Wire shape sent to `editSkill` — mirrors EditSkillRequest, WITHOUT
 *  `policy_class`: an edit can never mint or alter a system contract, so the
 *  field is deliberately absent from this surface. All fields are optional
 *  (PATCH merge). */
export interface EditSkillRequestBody {
  name?: string;
  version?: string;
  summary?: string;
  skill_md?: string;
  references?: Record<string, string>;
  assets?: Record<string, string>;
}

/** Minimal structural facts prefilled from the Slice-2 detail fetch. */
export interface EditPrefillFacts {
  name?: string;
  summary?: string;
  version?: string;
}

/** Seed the edit form from the existing skill. SKILL.md + reference/asset maps
 *  are NOT exposed by the detail fetch, so they start empty — blank SKILL.md
 *  keeps the current guidance (see module note), and the file editors describe
 *  themselves as replacement maps. */
export function prefillEditForm(facts: EditPrefillFacts): EditSkillFormValues {
  return {
    name: facts.name ?? '',
    version: facts.version ?? '',
    summary: facts.summary ?? '',
    skillMd: '',
    references: [],
    assets: [],
  };
}

/** Fold a list of name→content entries into a record, keeping only rows whose
 *  name is non-empty (a blank name row is an unfinished entry, not a file). */
function foldEntries(
  entries: NamedFileEntry[],
): Record<string, string> | undefined {
  const out: Record<string, string> = {};
  for (const e of entries) {
    const key = e.name.trim();
    if (key.length === 0) continue;
    out[key] = e.content;
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

/**
 * Assemble the `editSkill` PATCH body from the trimmed form, matching the
 * daemon merge (spec v3 §9.5):
 *   - name           — always sent (required; the guard blocks an empty name).
 *   - version        — sent when non-empty; omitted when blank so the stored
 *                      version is preserved (a skill can't have a blank version).
 *   - summary        — always sent (it is prefilled, so its field value IS the
 *                      intended state; clearing it is an intentional edit).
 *   - skill_md       — sent ONLY when non-blank; blank keeps the current stored
 *                      guidance (the daemon preserves it on omission).
 *   - references /
 *     assets         — sent ONLY when at least one named file is present; these
 *                      are replacement maps, so an empty editor sends nothing.
 * `policy_class` is never set — an edit can never mint a system contract.
 */
export function buildEditSkillRequest(
  values: EditSkillFormValues,
): EditSkillRequestBody {
  const body: EditSkillRequestBody = {
    name: values.name.trim(),
    summary: values.summary.trim(),
  };
  const version = values.version.trim();
  if (version.length > 0) body.version = version;
  const skillMd = values.skillMd.trim();
  if (skillMd.length > 0) body.skill_md = values.skillMd;
  const references = foldEntries(values.references);
  if (references) body.references = references;
  const assets = foldEntries(values.assets);
  if (assets) body.assets = assets;
  return body;
}

/** Client-side required-field guard BEFORE PATCH: only the name is required.
 *  Version + SKILL.md are optional on edit (blank preserves the stored value),
 *  and the daemon 422s only when NO editable field is supplied — which can't
 *  happen here because `name` is always sent. */
export function editFormErrors(values: EditSkillFormValues): string[] {
  const errors: string[] = [];
  if (values.name.trim().length === 0) errors.push('Add a name.');
  return errors;
}

export function isEditFormSubmittable(values: EditSkillFormValues): boolean {
  return editFormErrors(values).length === 0;
}

// ── Re-validation result ────────────────────────────────────────────────

/** Head line for a PASSED edit re-validation. When the version was bumped, the
 *  new version is not yet effective for already-assigned agents — it takes
 *  effect at each agent's next session (spec v3 §7.1); say so plainly. */
export function editSuccessHeadline(versionBumped: boolean): string {
  return versionBumped
    ? 'Saved — technical checks passed. The updated version takes effect for each assigned agent at its next session.'
    : 'Saved — technical checks passed. Your changes are in the catalog.';
}

// ── Edited-effective model ──────────────────────────────────────────────

/** True when the re-validation response carried a different version than the
 *  one loaded into the form — i.e. the operator bumped the version. */
export function isVersionBumped(before: string, after: string): boolean {
  return before.trim().length > 0 && before.trim() !== after.trim();
}

function isEffective(a: AgentAssignmentFacts): boolean {
  return a.assigned && (a.effective || a.state === 'effective');
}

/**
 * Re-shape the per-agent assignment facts for display AFTER a successful,
 * version-bumping edit: any agent for whom the skill was already effective
 * becomes assigned-but-not-yet-effective, because the just-saved version has
 * not materialized for them yet — it takes effect at their next session
 * (spec v3 §7.1 / §2). Non-effective rows are untouched. When the version was
 * NOT bumped the facts pass through unchanged. The caller feeds the result to
 * `agentProvenanceList` (skills-detail) so the product-language reasons stay
 * the single Slice-2 vocabulary, never a parallel copy.
 */
export function effectiveAfterEdit(
  assignments: AgentAssignmentFacts[],
  versionBumped: boolean,
): AgentAssignmentFacts[] {
  if (!versionBumped) return assignments;
  return assignments.map((a) =>
    isEffective(a)
      ? { ...a, effective: false, state: 'assigned_not_yet_effective' }
      : a,
  );
}

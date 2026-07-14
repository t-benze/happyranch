/**
 * Pure, provider-agnostic helpers for the Add / Import custom skill + the
 * validation-result surface (THR-092 Slice 3 of 6). All copy and mapping that
 * must obey the copy discipline lives HERE, apart from the JSX, so it is
 * unit-tested against the forbidden-word scan:
 *
 *   - Guidance-visibility language ONLY. Never permission / approve / admit /
 *     grant / materialize / pending, and never user-facing "active". Adding a
 *     custom skill changes what an agent is SHOWN as guidance — it never grants
 *     a tool, command, or permission (spec v3 §2/§3.1 invariants).
 *   - A custom skill is user_authored ONLY. This module never emits a
 *     `policy_class` (least of all `system_contract`) — the backend rejects a
 *     custom skill minting a system contract (spec v3 §3.4), and the UI must
 *     never offer it. `buildCreateSkillRequest` therefore has no policy_class
 *     input or output at all.
 *   - A failed technical validation is NOT a dead end (spec v3 §9.1a): the
 *     draft still persists (`skill_id`) and stays editable. The result copy
 *     frames failure as a fixable technical check, never a rejection.
 *
 * Inputs are minimal STRUCTURAL shapes (not the `@/lib/api/skills` types) so
 * this module stays decoupled from the data layer and clear of the features/*
 * no-restricted-imports rule (MEM-032).
 */

// ── Form → request ──────────────────────────────────────────────────────

/** One entry of an optional name→content map (references / assets). */
export interface NamedFileEntry {
  /** File name / key, e.g. `notes.md`. */
  name: string;
  /** File body. */
  content: string;
}

/** Raw form state for the add/import surface. All strings are pre-trim. */
export interface CreateSkillFormValues {
  slug: string;
  name: string;
  /** Optional (CreateSkillRequest.version?). */
  version: string;
  /** Optional (CreateSkillRequest.summary?). */
  summary: string;
  /** The SKILL.md body — the guidance itself. Required. */
  skillMd: string;
  references: NamedFileEntry[];
  assets: NamedFileEntry[];
}

/** Wire shape sent to `createSkill` — mirrors CreateSkillRequest, but WITHOUT
 *  `policy_class`: a custom skill is user_authored only and can never mint a
 *  system contract, so the field is deliberately absent from this surface. */
export interface CreateSkillRequestBody {
  slug: string;
  name: string;
  version?: string;
  summary?: string;
  skill_md: string;
  references?: Record<string, string>;
  assets?: Record<string, string>;
}

export function emptyCreateSkillForm(): CreateSkillFormValues {
  return {
    slug: '',
    name: '',
    version: '',
    summary: '',
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
 * Assemble the `createSkill` request from the trimmed form. Optional fields
 * (version, summary, references, assets) are OMITTED when empty rather than
 * sent blank, so the backend applies its own defaults. `policy_class` is never
 * set — custom skills are user_authored only.
 */
export function buildCreateSkillRequest(
  values: CreateSkillFormValues,
): CreateSkillRequestBody {
  const body: CreateSkillRequestBody = {
    slug: values.slug.trim(),
    name: values.name.trim(),
    skill_md: values.skillMd,
  };
  const version = values.version.trim();
  if (version.length > 0) body.version = version;
  const summary = values.summary.trim();
  if (summary.length > 0) body.summary = summary;
  const references = foldEntries(values.references);
  if (references) body.references = references;
  const assets = foldEntries(values.assets);
  if (assets) body.assets = assets;
  return body;
}

/** Client-side required-field guard BEFORE POST: slug, name, and the SKILL.md
 *  body must be present. Version is optional. This mirrors the daemon's
 *  malformed-request 422 (missing required request field) so the operator gets
 *  an inline nudge instead of a round-trip. */
export function createFormErrors(values: CreateSkillFormValues): string[] {
  const errors: string[] = [];
  if (values.slug.trim().length === 0) errors.push('Add a slug / id.');
  if (values.name.trim().length === 0) errors.push('Add a name.');
  if (values.skillMd.trim().length === 0)
    errors.push('Add the SKILL.md guidance body.');
  return errors;
}

export function isCreateFormSubmittable(
  values: CreateSkillFormValues,
): boolean {
  return createFormErrors(values).length === 0;
}

// ── Validation result ───────────────────────────────────────────────────

export type ValidationState = 'in_catalog' | 'validated' | 'failed_validation';

/** Minimal structural shape of a CreateSkillResponse / ValidateSkillResponse. */
export interface ValidationResultFacts {
  skill_id: string;
  validation_state: string;
  validation: { ok: boolean; errors?: string[] } | null | undefined;
}

/** Did the technical validation pass? A validated result is the only success;
 *  everything else persisted an editable draft that "needs attention". */
export function isValidationPassed(facts: ValidationResultFacts): boolean {
  if (facts.validation && facts.validation.ok === false) return false;
  if (facts.validation && facts.validation.ok === true) return true;
  return facts.validation_state === 'validated';
}

/**
 * Map ONE raw backend validation error to a plain-language, guidance-visibility
 * message. Recognised failure families get an actionable rewrite; anything
 * unrecognised passes through trimmed (the backend errors are already
 * human-readable). Never emits permission / approval wording.
 */
export function plainValidationError(raw: string): string {
  const e = raw.trim();
  const l = e.toLowerCase();
  if (/(system[_-]?contract|reserved field)/.test(l)) {
    return 'Custom skills can’t declare a system contract — remove that field. A custom skill stays a custom skill.';
  }
  if (/collide|collision|already (used|exists)|release skill/.test(l)) {
    return 'Choose a different slug — this one is already used by a bundled skill.';
  }
  if (/skill\.?md/.test(l) && /(missing|heading|empty|present)/.test(l)) {
    return 'Add the SKILL.md guidance body, including a heading, so agents know what the skill is.';
  }
  if (/version/.test(l) && /(missing|required|invalid)/.test(l)) {
    return 'Add a version to the skill’s details.';
  }
  if (/(id|slug|name)/.test(l) && /(missing|required)/.test(l)) {
    return 'Fill in the required details: id / slug, name, and version.';
  }
  if (/(reference|asset|could not.*resolv|unresolved|not.*found)/.test(l)) {
    return 'A referenced file couldn’t be found — include it, or remove the reference.';
  }
  if (/(parse|yaml|malformed|read)/.test(l)) {
    return 'The skill package couldn’t be read — check the formatting of skill.yaml and SKILL.md.';
  }
  return e;
}

/** Map + clean the whole error list (dropping blanks). */
export function plainValidationErrors(
  errors: string[] | undefined,
): string[] {
  return (errors ?? [])
    .filter((e) => e.trim().length > 0)
    .map(plainValidationError);
}

/** Head line for a PASSED validation — a saved, shown-as-guidance custom skill. */
export function successHeadline(): string {
  return 'Validated — technical checks passed. Your custom skill is saved to the catalog.';
}

/** Head line for a FAILED validation — a fixable technical check, never a
 *  rejection. The draft is kept and stays editable. */
export function failureHeadline(issueCount: number): string {
  const noun = issueCount === 1 ? 'item' : 'items';
  return `${issueCount} ${noun} to fix — this is a technical check, not a review gate. Your draft is kept in the catalog; fix the ${noun} below and re-validate.`;
}

// ── The validation-check explanation (guidance-only product language) ────

export interface ValidationCheck {
  key: string;
  title: string;
  description: string;
}

/**
 * Plain-language explanation of EVERY technical check the validator runs
 * (spec v3 §8.3 point 1), shown as guidance so the operator understands what
 * "validation" means — it is a correctness guard, never a trust / approval
 * gate. One entry per check the brief enumerates: parses / metadata / SKILL.md
 * present / references + assets resolve / no bundled-slug collision / custom
 * cannot mint a system contract / dry assembly.
 */
export const VALIDATION_CHECKS: ValidationCheck[] = [
  {
    key: 'parses',
    title: 'The package reads cleanly',
    description:
      'Your skill.yaml and SKILL.md are well-formed, with no broken formatting.',
  },
  {
    key: 'metadata',
    title: 'The required details are filled in',
    description:
      'The skill carries an id / slug, a name, and a version so it can be listed and referenced.',
  },
  {
    key: 'skill_md',
    title: 'The guidance itself is present',
    description:
      'SKILL.md holds the actual guidance text an agent will be shown — it is not empty.',
  },
  {
    key: 'files_resolve',
    title: 'References and assets resolve',
    description:
      'Every file the skill points to is included in the package and can be found.',
  },
  {
    key: 'slug_unique',
    title: 'The slug doesn’t clash with a bundled skill',
    description:
      'Your slug is distinct from every platform-bundled skill, so the right guidance always loads.',
  },
  {
    key: 'stays_custom',
    title: 'It stays a custom skill',
    description:
      'A custom skill can’t declare itself a system contract — that class is reserved for the platform.',
  },
  {
    key: 'dry_assembly',
    title: 'It assembles cleanly',
    description:
      'A trial assembly of the package into a skill folder succeeds, so agents can be shown it without error.',
  },
];

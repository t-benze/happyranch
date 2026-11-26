/**
 * Create team / Create agent form rules (THR-402, PRD
 * docs/product/prds/2026-11-26-team-and-agent-creation.md).
 *
 * Pure module (no React, no I/O) so the rules can be checked by a plain Node
 * script (`web/scripts/create-agent-form.test.mjs`).
 *
 * - Agent name: 1–64 ASCII characters, a letter first, then letters, digits,
 *   `_` or `-`. Sent as `display_name`; the server owns the permanent ID.
 * - New team name (manager + `new_team`): 1–64 characters, a lowercase ASCII
 *   letter first, then lowercase letters, digits or `_`. `default` (any case)
 *   is reserved for the Founder-managed Default team. Existing team keys that
 *   do not follow this rule stay selectable for workers; this rule only
 *   applies to NEW team names (shared API contract with the backend PR).
 * - Outcomes: a confirmed client-side refusal (4xx) changed nothing and the
 *   form may be corrected and resubmitted; `agent_setup_incomplete` (500)
 *   means the agent WAS created but its workspace setup did not finish; any
 *   other answer (no response, 408/499, other 5xx) is a result unknown.
 */

export const AGENT_NAME_RE = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
export const NEW_TEAM_RE = /^[a-z][a-z0-9_]{0,63}$/;

export type CreateRole = 'worker' | 'manager';
export type CreateMode = 'team' | 'agent';
export type CreateField = 'name' | 'team' | 'newTeam' | 'executor' | 'description' | 'systemPrompt';

/** The editable team suggestion for a manager name: lowercase, `-`→`_`, drop the last `_suffix`. */
export function suggestTeamName(name: string): string {
  if (!name) return '';
  const normalized = name.toLowerCase().replaceAll('-', '_');
  const i = normalized.lastIndexOf('_');
  if (i <= 0) return normalized;
  return normalized.slice(0, i);
}

export type NameProblem = 'required' | 'invalid' | null;
export type NewTeamProblem = 'required' | 'invalid' | 'reserved' | 'exists' | null;

export function agentNameProblem(name: string): NameProblem {
  if (name === '') return 'required';
  return AGENT_NAME_RE.test(name) ? null : 'invalid';
}

/** `existing`: current team keys, when known (null while unknown). */
export function newTeamProblem(team: string, existing: readonly string[] | null): NewTeamProblem {
  if (team === '') return 'required';
  if (team.toLowerCase() === 'default') return 'reserved';
  if (!NEW_TEAM_RE.test(team)) return 'invalid';
  if (existing !== null && existing.includes(team)) return 'exists';
  return null;
}

/** The request body for the current role: exactly one of `team` / `new_team`. */
export interface CreateDraft {
  name: string;
  role: CreateRole;
  team: string;
  newTeam: string;
  executor: string;
  description: string;
  systemPrompt: string;
}

export interface CreateBody {
  display_name: string;
  role: CreateRole;
  team?: string;
  new_team?: string;
  executor: string;
  description: string;
  system_prompt: string;
}

export function buildCreateBody(draft: CreateDraft): CreateBody {
  const base = {
    display_name: draft.name,
    role: draft.role,
    executor: draft.executor,
    description: draft.description,
    system_prompt: draft.systemPrompt,
  };
  return draft.role === 'worker' ? { ...base, team: draft.team } : { ...base, new_team: draft.newTeam };
}

/** Server codes that belong to one field (the rest of the form is kept). */
const FIELD_CODES: Record<string, CreateField> = {
  identity_name_unavailable: 'name',
  identity_name_reserved: 'name',
  invalid_identity_label: 'name',
  invalid_agent_name: 'name',
  agent_name_source_required: 'name',
  agent_id_exhausted: 'name',
  agent_exists: 'name',
  invalid_team_name: 'newTeam',
  team_reserved: 'newTeam',
  team_exists: 'newTeam',
  unknown_team: 'team',
  invalid_executor: 'executor',
};

export function fieldForCode(code: string | null | undefined): CreateField | null {
  if (!code) return null;
  return Object.prototype.hasOwnProperty.call(FIELD_CODES, code) ? FIELD_CODES[code]! : null;
}

export type CreateOutcome =
  /** The server refused before any effect: correct and resubmit. */
  | { kind: 'refused'; status: number; code: string | null }
  /** The agent and its team were created; workspace setup did not finish. */
  | { kind: 'setupIncomplete'; canonicalId: string | null; team: string | null; role: string | null }
  /** No trustworthy answer: the agent may or may not exist. */
  | { kind: 'unknown' };

const str = (value: unknown): string | null => (typeof value === 'string' && value !== '' ? value : null);

/** Classify a rejected create request. Matched by shape so this module stays import-free. */
export function classifyCreateError(error: unknown): CreateOutcome {
  if (!error || typeof error !== 'object') return { kind: 'unknown' };
  const { status, code, detail } = error as { status?: unknown; code?: unknown; detail?: unknown };
  if (typeof status !== 'number') return { kind: 'unknown' };
  if (code === 'agent_setup_incomplete') {
    const d = (detail && typeof detail === 'object' ? detail : {}) as Record<string, unknown>;
    return { kind: 'setupIncomplete', canonicalId: str(d.canonical_id), team: str(d.team), role: str(d.role) };
  }
  if (status === 408 || status === 499 || code === 'cancelled' || status >= 500) return { kind: 'unknown' };
  if (status === 409 && code === 'target_changed') return { kind: 'unknown' };
  if (status >= 400) return { kind: 'refused', status, code: typeof code === 'string' ? code : null };
  return { kind: 'unknown' };
}

/** Minimal roster/identity shapes the Check result compares against. */
export interface RosterRow {
  name: string;
  addressable_name?: string | null;
  team: string | null;
  role: string | null;
  executor: string | null;
  description: string | null;
  system_prompt?: string | null;
}

export interface IdentityRow {
  canonical_id: string;
  kind: string;
  addressable_name: string | null;
}

export type CheckFinding =
  /** No agent or identity carries the submitted name: nothing was created. */
  | { kind: 'absent' }
  /** An active agent matches every submitted field. */
  | { kind: 'matched'; canonicalId: string }
  /** Something carries this name, but that alone does not prove this request created it. */
  | { kind: 'nameOnly'; canonicalId: string | null };

const same = (a: string | null | undefined, b: string): boolean => (a ?? '').trim() === b.trim();

/**
 * Compare FRESH roster and identity reads with the body that was sent. Names
 * are compared case-insensitively (the naming service owns availability).
 */
export function findCreatedAgent(
  sent: CreateBody,
  roster: readonly RosterRow[],
  identities: readonly IdentityRow[],
): CheckFinding {
  const wanted = sent.display_name.toLowerCase();
  const nameOf = (value: string | null | undefined) => (value ?? '').toLowerCase();
  // Older rosters may omit the name metadata: fall back to the identity row.
  const rowName = (row: RosterRow) =>
    row.addressable_name ?? identities.find((identity) => identity.canonical_id === row.name)?.addressable_name;
  const rows = roster.filter((row) => nameOf(rowName(row)) === wanted);
  const team = sent.role === 'worker' ? sent.team ?? '' : sent.new_team ?? '';
  const full = rows.find(
    (row) =>
      row.role === sent.role &&
      row.team === team &&
      row.executor === sent.executor &&
      same(row.description, sent.description) &&
      (row.system_prompt === undefined || same(row.system_prompt, sent.system_prompt)),
  );
  if (full) return { kind: 'matched', canonicalId: full.name };
  if (rows.length > 0) return { kind: 'nameOnly', canonicalId: rows[0]!.name };
  const identity = identities.find((row) => row.kind === 'agent' && nameOf(row.addressable_name) === wanted);
  if (identity) return { kind: 'nameOnly', canonicalId: identity.canonical_id };
  return { kind: 'absent' };
}

/**
 * Tasks list URL filter model (THR-390 PR8, flag `task_attributes_ui`).
 *
 * URL: `?status=a&status=b&agent=<permanent id>&attr_min=effort%3D3&attr_set=ready`.
 * - `status` repeats (absent = all), `agent` is the assigned agent's permanent ID.
 * - `attr_*` occurrences are kept as the exact decoded strings and in URL order,
 *   so a hand-edited link is sent unchanged; only Apply writes canonical order.
 * - The export row limit and any cursor are never part of this URL.
 * Pure functions only: no React, no network.
 */
import { TASK_LIST_STATUSES } from '@/hooks/tasks';
import type { AttributeFilter, AttributeFilterParam } from '@/lib/api/types';

const ATTRIBUTE_FILTER_PARAMS: readonly AttributeFilterParam[] = ['attr_min', 'attr_max', 'attr_set', 'attr_not_set'];

export interface ListFilterModel {
  /** Selected statuses in TASK_LIST_STATUSES order; empty = all. */
  statuses: string[];
  agent: string | null;
  attributes: AttributeFilter[];
}

export const EMPTY_FILTERS: ListFilterModel = { statuses: [], agent: null, attributes: [] };

const OP_ORDER: Record<AttributeFilterParam, number> = { attr_min: 0, attr_max: 1, attr_set: 2, attr_not_set: 3 };
const KEY_RE = /^[a-z][a-z0-9_]{0,63}$/;
const NUMBER_RE = /^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$/;
export const MAX_FILTER_KEYS = 16;
export const MAX_FILTER_PREDICATES = 64;

const isParam = (name: string): name is AttributeFilterParam =>
  (ATTRIBUTE_FILTER_PARAMS as readonly string[]).includes(name);

export function parseListFilters(search: URLSearchParams): ListFilterModel {
  const wanted = new Set(search.getAll('status'));
  const statuses = TASK_LIST_STATUSES.filter((status) => wanted.has(status));
  const attributes: AttributeFilter[] = [];
  for (const [name, value] of search) if (isParam(name)) attributes.push([name, value]);
  return {
    // All seven selected is the same selection as none (no status param).
    statuses: statuses.length === TASK_LIST_STATUSES.length ? [] : statuses,
    agent: search.get('agent') || null,
    attributes,
  };
}

/** Key of one occurrence: range values split on the first `=`. */
export function clauseKey([name, value]: AttributeFilter): string {
  if (name === 'attr_min' || name === 'attr_max') {
    const at = value.indexOf('=');
    return at < 0 ? value : value.slice(0, at);
  }
  return value;
}

/** Bound of a range occurrence (null for set/not-set or a malformed value). */
export function clauseBound([name, value]: AttributeFilter): string | null {
  if (name !== 'attr_min' && name !== 'attr_max') return null;
  const at = value.indexOf('=');
  return at < 0 ? null : value.slice(at + 1);
}

export function canonicalAttributes(attributes: readonly AttributeFilter[]): AttributeFilter[] {
  return [...attributes].sort((a, b) => {
    const ka = clauseKey(a);
    const kb = clauseKey(b);
    if (ka !== kb) return ka < kb ? -1 : 1;
    return OP_ORDER[a[0]] - OP_ORDER[b[0]];
  });
}

/** Canonical URL: statuses (fixed order), agent, then attr clauses by key, then min/max/set/not_set. */
export function serializeListFilters(model: ListFilterModel): URLSearchParams {
  const qs = new URLSearchParams();
  const statuses = TASK_LIST_STATUSES.filter((status) => model.statuses.includes(status));
  if (statuses.length < TASK_LIST_STATUSES.length) for (const status of statuses) qs.append('status', status);
  if (model.agent) qs.set('agent', model.agent);
  for (const [name, value] of canonicalAttributes(model.attributes)) qs.append(name, value);
  return qs;
}

export function hasAnyFilter(model: ListFilterModel): boolean {
  return model.statuses.length > 0 || !!model.agent || model.attributes.length > 0;
}

/** Export maps one scalar `status`: all statuses (no param) or exactly one; 2–6 cannot export (NQ1). */
export function exportStatus(model: ListFilterModel): { ok: true; status?: string } | { ok: false } {
  if (model.statuses.length === 0) return { ok: true };
  if (model.statuses.length === 1) return { ok: true, status: model.statuses[0] };
  return { ok: false };
}

// ---------------------------------------------------------------------------
// Draft clauses (the filter form)
// ---------------------------------------------------------------------------

export type DraftOp = 'ge' | 'le' | 'set' | 'unset';
export interface DraftClause {
  id: number;
  key: string;
  op: DraftOp;
  value: string;
}

export const OP_PARAM: Record<DraftOp, AttributeFilterParam> = {
  ge: 'attr_min', le: 'attr_max', set: 'attr_set', unset: 'attr_not_set',
};
const PARAM_OP: Record<AttributeFilterParam, DraftOp> = {
  attr_min: 'ge', attr_max: 'le', attr_set: 'set', attr_not_set: 'unset',
};
export const needsValue = (op: DraftOp): boolean => op === 'ge' || op === 'le';

export function draftFromAttributes(attributes: readonly AttributeFilter[], nextId: () => number): DraftClause[] {
  return attributes.map((occurrence) => ({
    id: nextId(),
    key: clauseKey(occurrence),
    op: PARAM_OP[occurrence[0]],
    value: clauseBound(occurrence) ?? '',
  }));
}

export type DraftError =
  | 'pickAttribute' | 'valueRequired' | 'invalidNumber' | 'duplicate' | 'conflict' | 'tooMany';

/**
 * UX pre-check only (the server stays authoritative). Bounds are never checked
 * against definition constraints and an inverted range is left to the server.
 */
export function validateDraft(clauses: readonly DraftClause[]): Map<number, DraftError> {
  const errors = new Map<number, DraftError>();
  const seen = new Map<string, Set<DraftOp>>();
  for (const clause of clauses) {
    const ops = seen.get(clause.key) ?? new Set<DraftOp>();
    if (!clause.key || !KEY_RE.test(clause.key)) errors.set(clause.id, 'pickAttribute');
    else if (needsValue(clause.op) && clause.value === '') errors.set(clause.id, 'valueRequired');
    else if (needsValue(clause.op) && (clause.value.length > 128 || !NUMBER_RE.test(clause.value))) {
      errors.set(clause.id, 'invalidNumber');
    } else if (ops.has(clause.op)) errors.set(clause.id, 'duplicate');
    ops.add(clause.op);
    seen.set(clause.key, ops);
  }
  for (const clause of clauses) {
    if (errors.has(clause.id)) continue;
    const ops = seen.get(clause.key);
    if (ops?.has('unset') && (ops.has('set') || ops.has('ge') || ops.has('le'))) errors.set(clause.id, 'conflict');
  }
  const keys = new Set(clauses.map((clause) => clause.key));
  if (keys.size > MAX_FILTER_KEYS || clauses.length > MAX_FILTER_PREDICATES) {
    const last = clauses[clauses.length - 1];
    if (last && !errors.has(last.id)) errors.set(last.id, 'tooMany');
  }
  return errors;
}

export function attributesFromDraft(clauses: readonly DraftClause[]): AttributeFilter[] {
  return clauses.map((clause) => [
    OP_PARAM[clause.op],
    needsValue(clause.op) ? `${clause.key}=${clause.value}` : clause.key,
  ] as const);
}

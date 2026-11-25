/**
 * Task detail › Attributes model (THR-390 PR7). Pure helpers only.
 *
 * - Editability comes only from the server read: the task-level and per-field
 *   `editability {can_edit, refusal_reason.code}`. Nothing here reads task
 *   status; there is no lifecycle rule (product ruling seq34, correction 3).
 * - CAS is per field (PR2): each change carries the read `definition.version`
 *   as `expected_version` and the read `current.revision` as
 *   `expected_revision`, and one Save sends ONE atomic PUT (assumption A1).
 * - No client constraint pre-validation: numeric input is only parsed to a
 *   finite number; the server's 422 is the authority (assumption A4).
 */
import type {
  AttributeChange,
  AttributeErrorDetail,
  AttributeEvent,
  AttributeScalar,
  TaskAttributeRead,
  TaskAttributesRead,
} from '@/lib/api/types';
import type { MessageKey } from '@/lib/i18n';

/** Copy for a server `refusal_reason.code`, keyed only by that code. */
export function refusalKey(code: string | null | undefined): MessageKey {
  switch (code) {
    case 'not_task_owner_or_owning_manager':
    case 'session_not_current':
    case 'recovery_purpose_forbidden':
      return 'tasks.attributes.refusal.readonly';
    case 'authority_unknown':
      return 'tasks.attributes.refusal.authorityUnknown';
    default:
      return 'tasks.attributes.refusal.generic';
  }
}

/** The read-only codes that also carry the agent note. */
export const showsAgentNote = (code: string | null | undefined): boolean =>
  refusalKey(code) === 'tasks.attributes.refusal.readonly';

export const PROVENANCE_KEY: Record<TaskAttributeRead['provenance'], MessageKey> = {
  supplied_at_admission: 'tasks.attributes.prov.supplied',
  not_supplied_before_dispatch: 'tasks.attributes.prov.notSupplied',
  definition_added_later: 'tasks.attributes.prov.addedLater',
  timing_unknown: 'tasks.attributes.prov.unknown',
};

export const isArchivedField = (a: TaskAttributeRead): boolean =>
  a.definition.archived || a.editability.refusal_reason?.code === 'definition_archived';

/** A field the browser may write: task-level and per-field server authority, not archived. */
export const fieldWritable = (read: TaskAttributesRead, a: TaskAttributeRead): boolean =>
  read.editability.can_edit && a.editability.can_edit && !isArchivedField(a);

/** Latest value event of one field (events are append-only, oldest first). */
export function latestEvent(a: TaskAttributeRead | undefined): AttributeEvent | null {
  const events = a?.history.events ?? [];
  return events.length ? events[events.length - 1] : null;
}

// ---------------------------------------------------------------------------
// Draft
// ---------------------------------------------------------------------------

/** Numeric/string: unset | set (+ raw text). Boolean: unset | true | false. */
export type FieldMode = 'unset' | 'set' | 'true' | 'false';
export interface FieldDraft {
  mode: FieldMode;
  raw: string;
}
export type Draft = Record<string, FieldDraft>;

export function fieldDraftOf(a: TaskAttributeRead): FieldDraft {
  if (!a.current.present) return { mode: 'unset', raw: '' };
  const v = a.current.value;
  if (a.definition.type === 'boolean') return { mode: v === true ? 'true' : 'false', raw: '' };
  return { mode: 'set', raw: String(v) };
}

export function draftOf(read: TaskAttributesRead): Draft {
  return Object.fromEntries(read.attributes.map((a) => [a.definition.definition_id, fieldDraftOf(a)]));
}

/** The native scalar a draft stands for, `undefined` for unset, or a parse error. */
export function draftValue(type: string, d: FieldDraft): { ok: true; value: AttributeScalar | undefined } | { ok: false } {
  if (d.mode === 'unset') return { ok: true, value: undefined };
  if (type === 'boolean') return { ok: true, value: d.mode === 'true' };
  if (type === 'numeric') {
    const n = d.raw.trim() === '' ? NaN : Number(d.raw);
    return Number.isFinite(n) ? { ok: true, value: n } : { ok: false };
  }
  return { ok: true, value: d.raw };
}

/** True when the draft differs from the read value (presence or value). */
export function fieldChanged(a: TaskAttributeRead, d: FieldDraft | undefined): boolean {
  if (!d) return false;
  const parsed = draftValue(a.definition.type, d);
  if (!parsed.ok) return true;
  if (parsed.value === undefined) return a.current.present;
  return !a.current.present || a.current.value !== parsed.value;
}

export interface BuiltChanges {
  changes: AttributeChange[];
  /** Definition IDs whose numeric text does not parse (no PUT is sent). */
  parseErrors: string[];
}

/** Changed, writable fields only, in read order; one entry per field. */
export function buildChanges(read: TaskAttributesRead, draft: Draft): BuiltChanges {
  const changes: AttributeChange[] = [];
  const parseErrors: string[] = [];
  for (const a of read.attributes) {
    const id = a.definition.definition_id;
    const d = draft[id];
    if (!fieldWritable(read, a) || !fieldChanged(a, d)) continue;
    const parsed = draftValue(a.definition.type, d as FieldDraft);
    if (!parsed.ok) {
      parseErrors.push(id);
      continue;
    }
    const cas = { definition_id: id, expected_version: a.definition.version, expected_revision: a.current.revision };
    changes.push(parsed.value === undefined ? { ...cas, op: 'clear' } : { ...cas, op: 'set', value: parsed.value });
  }
  return { changes, parseErrors };
}

/** 422 `errors["changes.<i>.value"]` → definition_id through the submitted order. */
export function errorsByDefinition(
  errors: Record<string, string[]> | undefined,
  submitted: readonly AttributeChange[],
): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const [path, codes] of Object.entries(errors ?? {})) {
    const m = /^changes\.(\d+)(?:\.|$)/.exec(path);
    const change = m ? submitted[Number(m[1])] : undefined;
    const id = change?.definition_id ?? '';
    out[id] = [...(out[id] ?? []), ...codes];
  }
  return out;
}

export const VALUE_ERROR_KEY: Record<string, MessageKey> = {
  below_min: 'tasks.attributes.err.belowMin',
  above_max: 'tasks.attributes.err.aboveMax',
  expected_integer: 'tasks.attributes.err.integer',
  expected_finite_number: 'tasks.attributes.err.number',
  below_min_length: 'tasks.attributes.err.tooShort',
  above_max_length: 'tasks.attributes.err.tooLong',
  expected_string: 'tasks.attributes.err.other',
  expected_boolean: 'tasks.attributes.err.other',
};

// ---------------------------------------------------------------------------
// Write failures
// ---------------------------------------------------------------------------

export type SaveFailure =
  | { kind: 'unknown' }
  | { kind: 'failed' }
  | { kind: 'refused'; code: string }
  | { kind: 'validation'; errors: Record<string, string[]> }
  | {
      kind: 'conflict';
      code: string;
      current: TaskAttributesRead;
      conflicts: NonNullable<AttributeErrorDetail['conflicts']> | null;
    };

const asDetail = (d: unknown): AttributeErrorDetail | null =>
  d && typeof d === 'object' ? (d as AttributeErrorDetail) : null;

function currentTaskRead(detail: AttributeErrorDetail | null): TaskAttributesRead | null {
  const c = detail?.current as Partial<TaskAttributesRead> | undefined;
  return c && Array.isArray(c.attributes) && c.editability ? (c as TaskAttributesRead) : null;
}

/** Lost or ambiguous transport is `unknown`: it may have committed. */
export function classifySaveError(error: unknown, submitted: readonly AttributeChange[]): SaveFailure {
  if (!error || typeof error !== 'object') return { kind: 'unknown' };
  const { status, code, detail: raw } = error as { status?: unknown; code?: unknown; detail?: unknown };
  if (typeof status !== 'number' || status < 400) return { kind: 'unknown' };
  if (status === 408 || status === 499 || code === 'cancelled') return { kind: 'unknown' };
  const detail = asDetail(raw);
  const c = typeof code === 'string' ? code : detail?.code ?? '';
  if (status === 422 && detail?.errors) return { kind: 'validation', errors: errorsByDefinition(detail.errors, submitted) };
  if (status === 409) {
    const current = currentTaskRead(detail);
    if (current) return { kind: 'conflict', code: c, current, conflicts: detail?.conflicts ?? null };
  }
  if (status >= 500) return { kind: 'failed' };
  return { kind: 'refused', code: c };
}

/** Definition IDs of submitted changes that `current` now reports archived. */
export function archivedInConflict(current: TaskAttributesRead, submitted: readonly AttributeChange[]): string[] {
  const ids = new Set(submitted.map((c) => c.definition_id));
  return current.attributes.filter((a) => ids.has(a.definition.definition_id) && isArchivedField(a))
    .map((a) => a.definition.definition_id);
}

/** Plain-text draft for "Copy draft": `key: value` per changed field. */
export function draftClipboardText(read: TaskAttributesRead, draft: Draft): string {
  return read.attributes
    .filter((a) => fieldChanged(a, draft[a.definition.definition_id]))
    .map((a) => {
      const d = draft[a.definition.definition_id] as FieldDraft;
      return `${a.definition.key}: ${d.mode === 'unset' ? '(unset)' : a.definition.type === 'boolean' ? d.mode : JSON.stringify(d.raw)}`;
    })
    .join('\n');
}

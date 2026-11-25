/**
 * Pure rules for Settings › Task attributes (THR-390 PR6). No React.
 *
 * - Write failures are classified once: a definite server answer (with its
 *   PR2 `detail` envelope), an unknown outcome (the response never arrived),
 *   or not sent. Only a definite answer may release Retry.
 * - Reconciliation compares ALL five definition fields after PR2's documented
 *   defaults (`normCreate`/`normUpdate`); a version or key alone is never
 *   proof that this draft committed.
 * - Authority is never inferred here: callers render server `editability`.
 */
import type {
  AttributeDefinition,
  AttributeDefinitionEditability,
  AttributeDefinitionFields,
  AttributeDefinitionRead,
  AttributeErrorDetail,
} from '@/lib/api/types';

export type AttributeType = AttributeDefinition['type'];
export const ATTRIBUTE_TYPES: readonly AttributeType[] = ['numeric', 'string', 'boolean'];

/** The five fields a definition write can set. */
export interface DefinitionFields {
  key: string;
  type: string;
  name: string;
  description: string | null;
  constraints: Record<string, number | boolean>;
}

export type WriteFailure =
  | { kind: 'refused'; status: number; code: string | null; detail: AttributeErrorDetail | null }
  | { kind: 'unknown' };

function asDetail(detail: unknown): AttributeErrorDetail | null {
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return null;
  const code = (detail as { code?: unknown }).code;
  return typeof code === 'string' ? (detail as AttributeErrorDetail) : null;
}

/**
 * Classify a thrown write error by shape (`ApiError` status/code/detail).
 * No response (a network failure, a non-API error), 408, 499 `cancelled`
 * (the client's result-unknown answer) and 409 `target_changed` are UNKNOWN:
 * the write may or may not have applied, so only "Refresh to check" is
 * offered. Any other answered status ≥ 400 is a definite refusal with its
 * PR2 envelope; a 5xx is a definite failure, so Retry may be offered
 * (THR-390 PR6 design R6).
 */
export function classifyWriteError(error: unknown): WriteFailure {
  if (!error || typeof error !== 'object') return { kind: 'unknown' };
  const { status, code, detail } = error as { status?: unknown; code?: unknown; detail?: unknown };
  if (typeof status !== 'number' || status < 400) return { kind: 'unknown' };
  if (status === 408 || status === 499 || code === 'cancelled') return { kind: 'unknown' };
  if (status === 409 && code === 'target_changed') return { kind: 'unknown' };
  return { kind: 'refused', status, code: typeof code === 'string' ? code : null, detail: asDetail(detail) };
}

/** `detail.current` as a definition read, when the envelope carries one. */
export function currentDefinitionRead(detail: AttributeErrorDetail | null): AttributeDefinitionRead | null {
  const current = detail?.current as Partial<AttributeDefinitionRead> | undefined;
  return current && current.definition && current.editability ? (current as AttributeDefinitionRead) : null;
}

const sameConstraints = (a: Record<string, number | boolean>, b: Record<string, number | boolean>): boolean => {
  const ak = Object.keys(a).sort();
  const bk = Object.keys(b).sort();
  if (ak.length !== bk.length || ak.some((k, i) => k !== bk[i])) return false;
  return ak.every((k) => {
    const x = a[k];
    const y = b[k];
    if (typeof x === 'boolean' || typeof y === 'boolean') return x === y;
    return Number(x) === Number(y);
  });
};

/** PR2 defaults: name trimmed, description verbatim (omitted → null), constraints omitted/null → {}. */
export function normCreate(body: Partial<DefinitionFields>): DefinitionFields {
  return {
    key: body.key ?? '',
    type: body.type ?? '',
    name: (body.name ?? '').trim(),
    description: body.description === undefined ? null : body.description,
    constraints: body.constraints ?? {},
  };
}

/** PR2 merges each submitted field over the current definition, then normalises. */
export function normUpdate(pre: DefinitionFields, body: Partial<DefinitionFields>): DefinitionFields {
  return normCreate({
    key: body.key !== undefined ? body.key : pre.key,
    type: body.type !== undefined ? body.type : pre.type,
    name: body.name !== undefined ? body.name : pre.name,
    description: body.description !== undefined ? body.description : pre.description,
    constraints: body.constraints !== undefined && body.constraints !== null ? body.constraints : pre.constraints,
  });
}

export function fieldsOf(d: AttributeDefinition): DefinitionFields {
  return { key: d.key, type: d.type, name: d.name, description: d.description, constraints: d.constraints };
}

/** All five fields equal (constraints by key set and numeric value). */
export function sameFields(a: DefinitionFields, b: DefinitionFields): boolean {
  return a.key === b.key && a.type === b.type && a.name === b.name
    && a.description === b.description && sameConstraints(a.constraints, b.constraints);
}

export type CreateReconciliation =
  | { outcome: 'committed'; definition: AttributeDefinition }
  | { outcome: 'notSaved' }
  | { outcome: 'otherOwner'; owner: AttributeDefinition };

/** §5.1: committed only when the drafted key exists AND all five fields equal normCreate(sent body). */
export function reconcileCreate(definitions: AttributeDefinition[], sent: AttributeDefinitionFields): CreateReconciliation {
  const row = definitions.find((d) => d.key === sent.key);
  if (!row) return { outcome: 'notSaved' };
  return sameFields(fieldsOf(row), normCreate(sent)) ? { outcome: 'committed', definition: row } : { outcome: 'otherOwner', owner: row };
}

export type EditReconciliation = { outcome: 'committed' } | { outcome: 'notSaved' } | { outcome: 'conflict' };

/** §6.1: committed only at expected+1 with all five fields equal normUpdate(pre, body). */
export function reconcileEdit(
  read: AttributeDefinition,
  pre: AttributeDefinition,
  sent: Partial<DefinitionFields> & { expected_version: number },
): EditReconciliation {
  if (read.version === sent.expected_version) return { outcome: 'notSaved' };
  const { expected_version: _expected, ...body } = sent;
  if (read.version === sent.expected_version + 1 && sameFields(fieldsOf(read), normUpdate(fieldsOf(pre), body))) {
    return { outcome: 'committed' };
  }
  return { outcome: 'conflict' };
}

export type ArchiveReconciliation =
  | { outcome: 'committed' }
  | { outcome: 'notArchived' }
  | { outcome: 'byOther' }
  | { outcome: 'stale' };

/** C2-06: an archive changes no field, so a commit equals the confirmed read at expected+1, archived. */
export function reconcileArchive(read: AttributeDefinition, pre: AttributeDefinition, expectedVersion: number): ArchiveReconciliation {
  if (!read.archived && read.version === expectedVersion) return { outcome: 'notArchived' };
  if (read.archived && read.version === expectedVersion + 1 && sameFields(fieldsOf(read), fieldsOf(pre))) return { outcome: 'committed' };
  return read.archived ? { outcome: 'byOther' } : { outcome: 'stale' };
}

/** A successful update echoes the stored definition; success only if every sent field is reflected. */
export function echoReflects(read: AttributeDefinition, pre: AttributeDefinition, body: Partial<DefinitionFields>): boolean {
  return sameFields(fieldsOf(read), normUpdate(fieldsOf(pre), body));
}

export const anyEditAllowed = (e: AttributeDefinitionEditability): boolean => e.can_edit_metadata || e.can_edit_structure;
export const allDenied = (e: AttributeDefinitionEditability): boolean =>
  !e.can_create && !e.can_edit_metadata && !e.can_edit_structure && !e.can_archive;

/** Count whole-character length (code points), as the server's `len()`. */
export const charLength = (text: string): number => Array.from(text).length;

/** Text for the clipboard "Copy draft" action: the draft's own fields, verbatim. */
export function draftClipboardText(draft: Partial<DefinitionFields>): string {
  return JSON.stringify(draft, null, 2);
}

/**
 * Pure capacity domain model for the Daemon / Capacity panel.
 *
 * Everything here is deliberately free of React so the state semantics can be
 * asserted directly. Three independent objects plus a submission record
 * (settled semantics S1):
 *
 *   base       — accepted only from a usable read or a usable success
 *   draft      — what the operator is editing
 *   latest     — an observation; NEVER writes base on its own
 *   submission — captured immutably BEFORE the request is awaited
 *
 * Numeric honesty (Q8) is NOT solved here and must never be claimed solved.
 * Two sites exist and only one is fixable in this radius:
 *
 *   Write site (fixed): the operator's input TEXT is validated with a canonical
 *   positive-decimal grammar BEFORE any `Number` conversion, so
 *   `9007199254740993` is refused rather than silently sent as
 *   `9007199254740992`.
 *
 *   Read site (NOT recoverable here): `client.ts` does
 *   `JSON.parse(await res.text())` and discards the text, so an unsafe raw
 *   token has already been rounded before this module sees it. The guard below
 *   therefore CLASSIFIES and does not repair: consumed numerics must be safe
 *   integers, and an unsafe one withholds the number rather than showing a
 *   rounded stand-in. A parsed safe integer cannot prove an exact or integral
 *   raw token — raw `9007199254740990.5` parses to `9007199254740990` and raw
 *   `1.0000000000000001` parses to `1`, both safe integers that PASS this
 *   guard. That false accept is characterized, not fixed; closing the read site
 *   is a shared-transport/API decision outside this radius.
 */
import type { DaemonCapacitySnapshot } from '@/lib/api/types';
import { translate, type Locale, type MessageKey } from '@/lib/i18n';

export interface CapacityPair {
  queue_workers: number;
  host_global_session_cap: number;
}

export interface CapacityKeyPresence {
  queue_workers: boolean;
  host_global_session_cap: boolean;
}

export interface CapacityBase {
  pair: CapacityPair;
  /** Absent is NOT equality with anything; comparisons are on (presence, value). */
  keyPresence: CapacityKeyPresence;
  revision: string;
}

export interface CapacitySubmission {
  pair: CapacityPair;
  baseRevision: string;
  reason: string;
  ack: boolean;
}

/** Identity of the environment override an acknowledgment was given for (S8). */
export interface AckContext {
  shadowedKeys: string[];
  resolvedValues: Record<string, number | null>;
}

export type FieldName = 'queue_workers' | 'host_global_session_cap' | 'rationale';

// ---------------------------------------------------------------------------
// Write-site numeric grammar — canonical positive decimal BEFORE Number (G3)
// ---------------------------------------------------------------------------

export type NumericTextRejection = 'blank' | 'grammar' | 'representation';

export type NumericTextResult =
  | { ok: true; value: number }
  | { ok: false; reason: NumericTextRejection };

/** Canonical positive decimal: no sign, no leading zero, no point, no exponent. */
const CANONICAL_POSITIVE_DECIMAL = /^[1-9][0-9]*$/;

/**
 * Validate operator input TEXT before any `Number` conversion.
 *
 * Order matters and is the manager-accepted G3 order: grammar on the TEXT,
 * then a `BigInt` magnitude bound, then a `String(Number(text)) === text`
 * round-trip. `Number()` is only reached once the text is already known to be a
 * canonical positive decimal within the exactly-representable range, so no
 * entered value is ever silently rounded.
 *
 * This bound is an EDITOR REPRESENTATION LIMIT surfaced as an editor error. It
 * is not, and must never be presented as, an API maximum or a host-safe range —
 * the backend contract remains an unbounded positive integer.
 */
export function parseCapacityText(text: string): NumericTextResult {
  if (text.length === 0) return { ok: false, reason: 'blank' };
  if (!CANONICAL_POSITIVE_DECIMAL.test(text)) return { ok: false, reason: 'grammar' };
  if (BigInt(text) > BigInt(Number.MAX_SAFE_INTEGER)) {
    return { ok: false, reason: 'representation' };
  }
  const value = Number(text);
  if (String(value) !== text) return { ok: false, reason: 'representation' };
  return { ok: true, value };
}

/**
 * Localized field-level rejection copy. `fieldLabel` is the already-localized
 * field label; `locale` is explicit because this module never calls hooks.
 */
export function numericTextMessage(
  reason: NumericTextRejection,
  fieldLabel: string,
  locale: Locale,
): string {
  if (reason === 'blank') return translate(locale, 'settings.capacity.numeric.blank', { field: fieldLabel });
  if (reason === 'grammar') {
    return translate(locale, 'settings.capacity.numeric.grammar', { field: fieldLabel });
  }
  return translate(locale, 'settings.capacity.numeric.representation', { field: fieldLabel });
}

export const REASON_MAX_LENGTH = 1000;

// ---------------------------------------------------------------------------
// Read-site guards — classify, never repair
// ---------------------------------------------------------------------------
//
// The classifier itself now lives with the capacity provider
// (`@/design-system/providers/_capacity-ordering`) so the PROVIDER and the VIEW
// share ONE definition of "usable" (R7). It is re-exported here under its
// established names so every existing consumer and test keeps its import.

export { isSafeCapacityNumber, classifyCapacitySnapshot as classifySnapshot } from '@/design-system/providers/_capacity-ordering';
export type { CapacityClassification as SnapshotClassification } from '@/design-system/providers/_capacity-ordering';

// ---------------------------------------------------------------------------
// Base, presence and acknowledgment identity
// ---------------------------------------------------------------------------

/**
 * Seed a base from a usable snapshot. Fields seed from `persisted_yaml` when a
 * key is present and from `next_start` when it is absent; absence is recorded
 * so a later comparison is on (presence, value), never value alone.
 */
export function baseFromSnapshot(snapshot: DaemonCapacitySnapshot): CapacityBase {
  const workersPresent = snapshot.persisted_yaml.queue_workers !== null;
  const capPresent = snapshot.persisted_yaml.host_global_session_cap !== null;
  return {
    pair: {
      queue_workers: workersPresent
        ? (snapshot.persisted_yaml.queue_workers as number)
        : snapshot.next_start.queue_workers,
      host_global_session_cap: capPresent
        ? (snapshot.persisted_yaml.host_global_session_cap as number)
        : snapshot.next_start.host_global_session_cap,
    },
    keyPresence: { queue_workers: workersPresent, host_global_session_cap: capPresent },
    revision: snapshot.revision,
  };
}

export function ackContextOf(snapshot: DaemonCapacitySnapshot): AckContext {
  const shadowedKeys = [...snapshot.environment_shadowed].sort();
  const resolvedValues: Record<string, number | null> = {};
  for (const key of shadowedKeys) {
    const next = snapshot.next_start as unknown as Record<string, number | undefined>;
    resolvedValues[key] = next[key] ?? null;
  }
  return { shadowedKeys, resolvedValues };
}

/**
 * Acknowledgment identity (S8). The ack resets if and only if this changes —
 * by key set OR by resolved value. Warning text, `revision` and
 * `effective_admission_cap` are deliberately NOT part of the identity:
 * the daemon resolves each shadowed key's value from the environment while the
 * warning is a constant string and the revision is derived from file bytes, so
 * an environment change can present an identical revision with a different
 * resolved value; and an unrelated `effective_admission_cap` change must not
 * over-reset a still-valid acknowledgment.
 */
export function sameAckContext(a: AckContext | null, b: AckContext | null): boolean {
  if (a === null || b === null) return a === b;
  if (a.shadowedKeys.length !== b.shadowedKeys.length) return false;
  if (a.shadowedKeys.some((key, index) => key !== b.shadowedKeys[index])) return false;
  return a.shadowedKeys.every((key) => a.resolvedValues[key] === b.resolvedValues[key]);
}

/** Values the daemon will actually use next start, per key (3.x). */
export function resolvedNextStart(
  snapshot: DaemonCapacitySnapshot,
  draft: CapacityPair,
): { pair: CapacityPair; shadowedKeys: string[] } {
  const shadowed = new Set(snapshot.environment_shadowed);
  return {
    // Each field is computed independently: only a shadowed key falls back to
    // the environment-resolved value; an unshadowed key takes the draft.
    pair: {
      queue_workers: shadowed.has('queue_workers')
        ? snapshot.next_start.queue_workers
        : draft.queue_workers,
      host_global_session_cap: shadowed.has('host_global_session_cap')
        ? snapshot.next_start.host_global_session_cap
        : draft.host_global_session_cap,
    },
    shadowedKeys: [...snapshot.environment_shadowed].sort(),
  };
}

/** Catalog keys for the two capacity field labels (config keys stay verbatim). */
export const CAPACITY_FIELD_LABEL_KEYS: Record<string, MessageKey> = {
  queue_workers: 'settings.capacity.field.queue_workers',
  host_global_session_cap: 'settings.capacity.field.host_global_session_cap',
};

/** Localized field label; an unknown key renders verbatim. */
export function capacityFieldLabel(key: string, locale: Locale): string {
  const messageKey = CAPACITY_FIELD_LABEL_KEYS[key];
  return messageKey === undefined ? key : translate(locale, messageKey);
}

// ---------------------------------------------------------------------------
// Draft consequence arithmetic
// ---------------------------------------------------------------------------

export type ConsequenceResult =
  | {
    status: 'ok';
    /** The per-key values the daemon would actually use next start (R6). */
    resolved: CapacityPair;
    workerPoolTotal: number;
    nonTaskContribution: number;
    direction: 'below' | 'aligned' | 'above';
  }
  /** Server inconsistency: a negative non-task contribution. */
  | { status: 'inconsistent' }
  /** Derived sum left the exactly-representable range. */
  | { status: 'unrepresentable' };

/**
 * Worker-pool arithmetic for the draft. The non-task contribution comes only
 * from response metadata — no literal producer count is assumed — and both the
 * contribution and the derived SUM are guarded: individually safe inputs can
 * still sum outside the safe range, and an inconsistent response can make the
 * contribution negative.
 *
 * R6: the arithmetic runs on the RESOLVED per-key next-start pair, not the raw
 * draft. A shadowed key never reaches the daemon from this editor, so a draft
 * W of 5 under an environment-resolved W of 3 contributes 3 to the pool, and
 * the direction compares the RESOLVED cap against that resolved total. Using
 * the draft here claimed a consequence the environment override makes false
 * (accepted case 3.1). No future `effective_admission_cap` is predicted.
 */
export function draftConsequence(
  snapshot: DaemonCapacitySnapshot,
  draft: CapacityPair,
): ConsequenceResult {
  const nonTaskContribution =
    snapshot.producer_envelope - snapshot.producer_components.task_workers;
  if (!Number.isSafeInteger(nonTaskContribution)) return { status: 'unrepresentable' };
  if (nonTaskContribution < 0) return { status: 'inconsistent' };

  const resolved = resolvedNextStart(snapshot, draft).pair;
  const workerPoolTotal = resolved.queue_workers + nonTaskContribution;
  if (!Number.isSafeInteger(workerPoolTotal)) return { status: 'unrepresentable' };

  const direction = resolved.host_global_session_cap < workerPoolTotal
    ? 'below'
    : resolved.host_global_session_cap > workerPoolTotal
      ? 'above'
      : 'aligned';
  return { status: 'ok', resolved, workerPoolTotal, nonTaskContribution, direction };
}

export function consequenceMessage(
  direction: 'below' | 'aligned' | 'above',
  cap: number,
  total: number,
  locale: Locale,
): string {
  // Numbers are passed as `String(n)` so they render byte-identically in every
  // locale (no locale grouping).
  const params = { cap: String(cap), total: String(total) };
  if (direction === 'below') {
    return translate(locale, 'settings.capacity.consequence.below', params);
  }
  if (direction === 'above') {
    return translate(locale, 'settings.capacity.consequence.above', params);
  }
  return translate(locale, 'settings.capacity.consequence.aligned');
}

// ---------------------------------------------------------------------------
// Comparison helpers
// ---------------------------------------------------------------------------

/** Equality on (presence, value) — absent is never equal to a present value. */
export function sameBaseValues(a: CapacityBase, b: CapacityBase): boolean {
  return a.pair.queue_workers === b.pair.queue_workers
    && a.pair.host_global_session_cap === b.pair.host_global_session_cap
    && a.keyPresence.queue_workers === b.keyPresence.queue_workers
    && a.keyPresence.host_global_session_cap === b.keyPresence.host_global_session_cap;
}

export function formatPresenceValue(
  value: number,
  present: boolean,
  locale: Locale,
): string {
  return present ? String(value) : translate(locale, 'settings.capacity.notSetInFile');
}

/** "Last received HH:MM:SS (this browser's clock)" — never a server age. */
export function formatReceipt(receiptAt: number | null, locale: Locale = 'en'): string | null {
  if (receiptAt === null) return null;
  const at = new Date(receiptAt);
  const pad = (n: number) => String(n).padStart(2, '0');
  return translate(locale, 'settings.capacity.receipt', {
    time: `${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())}`,
  });
}

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

export function numericTextMessage(
  reason: NumericTextRejection,
  fieldLabel: string,
): string {
  if (reason === 'blank') return `${fieldLabel} is required.`;
  if (reason === 'grammar') {
    return `${fieldLabel} must be a whole number greater than zero, written in plain digits.`;
  }
  return `${fieldLabel} is outside the range this editor can represent exactly. The entered text is unchanged. This is a limit of this editor, not a limit of the daemon.`;
}

export const REASON_MAX_LENGTH = 1000;

// ---------------------------------------------------------------------------
// Read-site guards — classify, never repair
// ---------------------------------------------------------------------------

/** A consumed numeric is usable only if it survived JSON.parse as a safe integer. */
export function isSafeCapacityNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value);
}

function isNullableSafe(value: unknown): boolean {
  return value === null || isSafeCapacityNumber(value);
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

export type SnapshotClassification =
  | { status: 'usable'; snapshot: DaemonCapacitySnapshot }
  /** Shape/type defect, or a representation the editor cannot show exactly. */
  | { status: 'unusable'; reason: 'shape' | 'representation' }
  /** Structurally fine, but internally contradictory arithmetic. */
  | { status: 'inconsistent'; snapshot: DaemonCapacitySnapshot };

const PRODUCER_COMPONENT_KEYS = [
  'task_workers',
  'thread_workers',
  'dream_workers',
  'wake_workers',
  'schedule_workers',
] as const;

/**
 * Classify a snapshot that arrived from the wire (a GET body, a PUT success
 * body, a 409 `latest`, or a reread after an uncertain outcome). The same rule
 * applies at every entry point — an unusable value never enters accepted base
 * or cache as usable data.
 */
export function classifySnapshot(raw: unknown): SnapshotClassification {
  if (!isObject(raw)) return { status: 'unusable', reason: 'shape' };

  const shapeOk =
    typeof raw.revision === 'string' && raw.revision.length > 0
    && typeof raw.running_provenance === 'string'
    && typeof raw.effective_admission_reason === 'string'
    && typeof raw.authorization === 'string'
    && typeof raw.restart_required === 'boolean'
    && typeof raw.restart_pending === 'boolean'
    && (raw.environment_warning === null || typeof raw.environment_warning === 'string')
    && isStringArray(raw.environment_shadowed)
    && isStringArray(raw.warnings)
    && isObject(raw.running_at_daemon_start)
    && isObject(raw.persisted_yaml)
    && isObject(raw.next_start)
    && isObject(raw.producer_components)
    && isObject(raw.guidance)
    && typeof (raw.guidance as Record<string, unknown>).queue_workers === 'string'
    && typeof (raw.guidance as Record<string, unknown>).host_global_session_cap === 'string'
    && typeof (raw.guidance as Record<string, unknown>).enforced === 'boolean';
  if (!shapeOk) return { status: 'unusable', reason: 'shape' };

  const running = raw.running_at_daemon_start as Record<string, unknown>;
  const persisted = raw.persisted_yaml as Record<string, unknown>;
  const next = raw.next_start as Record<string, unknown>;
  const components = raw.producer_components as Record<string, unknown>;

  // Domain matrix (15.10): persisted_yaml.* and effective_admission_cap are the
  // only nullable numerics; everything else must be present. A null in a
  // non-nullable position is an unusable read, NEVER a zero.
  const presentOk =
    ('queue_workers' in persisted) && ('host_global_session_cap' in persisted)
    && running.queue_workers !== undefined && running.host_global_session_cap !== undefined
    && next.queue_workers !== undefined && next.host_global_session_cap !== undefined
    && raw.producer_envelope !== undefined
    && 'effective_admission_cap' in raw
    && PRODUCER_COMPONENT_KEYS.every((key) => components[key] !== undefined);
  if (!presentOk) return { status: 'unusable', reason: 'shape' };

  // Anything non-numeric (a quoted "3", a boolean, an array) is a shape defect.
  const numericSlots: unknown[] = [
    running.queue_workers, running.host_global_session_cap,
    next.queue_workers, next.host_global_session_cap,
    raw.producer_envelope,
    ...PRODUCER_COMPONENT_KEYS.map((key) => components[key]),
  ];
  const nullableSlots: unknown[] = [
    persisted.queue_workers, persisted.host_global_session_cap, raw.effective_admission_cap,
  ];
  if (numericSlots.some((slot) => typeof slot !== 'number')) {
    return { status: 'unusable', reason: 'shape' };
  }
  if (nullableSlots.some((slot) => slot !== null && typeof slot !== 'number')) {
    return { status: 'unusable', reason: 'shape' };
  }

  // Representation: a value that did not survive JSON.parse as a safe integer
  // cannot be displayed exactly, so it is withheld rather than shown rounded.
  if (!numericSlots.every(isSafeCapacityNumber)) {
    return { status: 'unusable', reason: 'representation' };
  }
  if (!nullableSlots.every(isNullableSafe)) {
    return { status: 'unusable', reason: 'representation' };
  }

  const snapshot = raw as unknown as DaemonCapacitySnapshot;

  // Domain: W and H strictly positive; producer components nonnegative.
  const positives = [
    snapshot.running_at_daemon_start.queue_workers,
    snapshot.running_at_daemon_start.host_global_session_cap,
    snapshot.next_start.queue_workers,
    snapshot.next_start.host_global_session_cap,
  ];
  if (positives.some((value) => value <= 0)) return { status: 'unusable', reason: 'shape' };
  if (PRODUCER_COMPONENT_KEYS.some((key) => snapshot.producer_components[key] < 0)) {
    return { status: 'unusable', reason: 'shape' };
  }
  if (snapshot.producer_envelope < 0) return { status: 'unusable', reason: 'shape' };
  if (
    snapshot.persisted_yaml.queue_workers !== null
    && snapshot.persisted_yaml.queue_workers <= 0
  ) {
    return { status: 'unusable', reason: 'shape' };
  }
  if (
    snapshot.persisted_yaml.host_global_session_cap !== null
    && snapshot.persisted_yaml.host_global_session_cap <= 0
  ) {
    return { status: 'unusable', reason: 'shape' };
  }

  // Relational invariant: task workers are part of the producer envelope. A
  // violation is a server inconsistency, not a number to render negatively.
  if (snapshot.producer_components.task_workers > snapshot.producer_envelope) {
    return { status: 'inconsistent', snapshot };
  }

  return { status: 'usable', snapshot };
}

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

export const CAPACITY_FIELD_LABELS: Record<string, string> = {
  queue_workers: 'Task session slots',
  host_global_session_cap: 'Host session admission limit',
};

// ---------------------------------------------------------------------------
// Draft consequence arithmetic
// ---------------------------------------------------------------------------

export type ConsequenceResult =
  | { status: 'ok'; workerPoolTotal: number; nonTaskContribution: number; direction: 'below' | 'aligned' | 'above' }
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
 */
export function draftConsequence(
  snapshot: DaemonCapacitySnapshot,
  draft: CapacityPair,
): ConsequenceResult {
  const nonTaskContribution =
    snapshot.producer_envelope - snapshot.producer_components.task_workers;
  if (!Number.isSafeInteger(nonTaskContribution)) return { status: 'unrepresentable' };
  if (nonTaskContribution < 0) return { status: 'inconsistent' };

  const workerPoolTotal = draft.queue_workers + nonTaskContribution;
  if (!Number.isSafeInteger(workerPoolTotal)) return { status: 'unrepresentable' };

  const direction = draft.host_global_session_cap < workerPoolTotal
    ? 'below'
    : draft.host_global_session_cap > workerPoolTotal
      ? 'above'
      : 'aligned';
  return { status: 'ok', workerPoolTotal, nonTaskContribution, direction };
}

export function consequenceMessage(direction: 'below' | 'aligned' | 'above', cap: number, total: number): string {
  if (direction === 'below') {
    return `Host cap ${cap} is below the worker-pool total ${total}. Additional sessions will wait if more request admission than the limit allows. Saving remains permitted.`;
  }
  if (direction === 'above') {
    return `Host cap ${cap} is above the worker-pool total ${total}. Extra admission room does not create additional producers.`;
  }
  return 'These configured limits align. Provider and runtime conditions may still limit work.';
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
): string {
  return present ? String(value) : 'Not set in file';
}

/** "Last received HH:MM:SS (this browser's clock)" — never a server age. */
export function formatReceipt(receiptAt: number | null): string | null {
  if (receiptAt === null) return null;
  const at = new Date(receiptAt);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `Last received ${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())} (this browser's clock)`;
}

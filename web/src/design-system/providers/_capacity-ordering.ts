/**
 * Capacity-only read/write ordering, receipt ownership and snapshot shape.
 *
 * Scope fence (TASK-8537 manager disposition G1/G2): everything here belongs to
 * the **capacity slot** of `SettingsApi`. `QueryLike`, `client.ts` and every
 * other domain are untouched. No `AbortSignal` plumbing is added — the shared
 * transport is excluded, and cancellation is not proof against a read issued
 * during a write (settled semantics S5-R8).
 *
 * The rules implemented here are S5-R1..R8 of the accepted design:
 *
 *  - A module-scoped monotonic `seq` stamps every issued read and every write.
 *  - Among reads, only the highest `issuedSeq` seen so far may publish (S5-R2).
 *    An older GET settling after a newer one is dropped, never merged.
 *  - A read whose `issuedSeq` precedes the accepted write's `settledSeq` may
 *    never publish, whatever its outcome and whenever it settles (S5-R1/R8) —
 *    the capacity GET carries no server timestamp or snapshot age, so the
 *    frontend cannot know whether the server handled it before or after the
 *    write.
 *  - An obsolete failed/malformed result never downgrades a state a newer
 *    usable result already recovered (S5-R3). Outcome grants no ordering
 *    privilege.
 *  - `receiptAt` advances on EVERY genuine successful network response —
 *    including one carrying structurally shared, byte-identical data — and on
 *    NO cache reread, remount, component effect or dropped read (S5-R5, S2).
 *  - Sequence numbers are local ordering tools only. They are never evidence of
 *    server snapshot age, ordering or atomicity and must never be rendered
 *    (S5-R6).
 */
import type { DaemonCapacitySnapshot } from '@/lib/api/types';

/** Provider-owned observation of one capacity request. */
export interface CapacityObservation {
  /** Assigned when the request is issued. Local ordering only (S5-R6). */
  issuedSeq: number;
  /** Assigned when the request settles. Local ordering only (S5-R6). */
  settledSeq: number;
  outcome: 'usable' | 'unusable' | 'failed';
  /**
   * Which request produced this observation.
   *
   * A snapshot published by our OWN accepted write is not an external change,
   * and the view must be able to tell the difference: the cache write inside
   * the mutation's `onSuccess` notifies the query observer, so in a real
   * browser the read-acceptance effect can run with the write's snapshot
   * BEFORE the submit handler's continuation accepts it. Without this field the
   * view manufactures a phantom "changed elsewhere" against the very revision
   * it just saved.
   */
  origin: 'read' | 'write';
  /**
   * Browser receipt of the last genuine successful network response, or null
   * when none has been received. Never a server observation age.
   */
  receiptAt: number | null;
  sourceRevision: string | null;
}

/** The capacity slot's query shape: `QueryLike` plus refresh/receipt/ordering. */
export interface CapacityQueryLike<T> {
  data: T | undefined;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
  /** Operator-driven "Refresh running state". */
  refetch: () => Promise<unknown>;
  isFetching: boolean;
  /** Provider-owned; null until the first request settles. */
  observation: CapacityObservation | null;
}

/** The settlement one capacity write REQUEST produced. Local ordering only (S5-R6). */
export interface CapacityWriteSettlement {
  settledSeq: number;
  /** Whether that settlement was accepted as a usable snapshot. */
  outcome: 'usable' | 'unusable';
}

/**
 * The capacity slot's mutation shape: `MutationLike` plus `settlementOf`.
 *
 * C3: the observation is module-scoped by org, so every mounted editor sees
 * every write. `settlementOf(request)` answers, for the exact request object an
 * editor passed to `mutateAsync`, which settlement THAT request produced — or
 * null when it has not settled with a response (pending, rejected, failed). The
 * editor that issued the request can therefore recognise its own settlement
 * exactly, instead of inferring it from timing or from a content revision.
 * `MutationLike` itself is deliberately NOT widened.
 */
export interface CapacityMutationLike<TArgs extends object, TResult> {
  mutateAsync: (args: TArgs) => Promise<TResult>;
  isPending: boolean;
  settlementOf: (request: TArgs) => CapacityWriteSettlement | null;
}

export const capacityQueryKey = (slug: string): readonly [string, string] => [
  'daemon-capacity',
  slug,
];

interface CapacityLedger {
  /** Highest read `issuedSeq` that has published (S5-R2). */
  highestPublishedReadSeq: number;
  /** The accepted write's `settledSeq` (S5-R1 as amended; S5-R8). */
  baseAcceptedSeq: number;
  observation: CapacityObservation | null;
}

const ledgers = new Map<string, CapacityLedger>();
let seqCounter = 0;

/** Monotonic local sequence. Never rendered, never a server-time claim. */
export function nextCapacitySeq(): number {
  seqCounter += 1;
  return seqCounter;
}

export function capacityLedger(slug: string): CapacityLedger {
  let ledger = ledgers.get(slug);
  if (!ledger) {
    ledger = { highestPublishedReadSeq: 0, baseAcceptedSeq: 0, observation: null };
    ledgers.set(slug, ledger);
  }
  return ledger;
}

/**
 * True when a settling read must be dropped: it is older than a read that
 * already published (S5-R2), or it was issued before the accepted write
 * finished (S5-R1/S5-R8).
 */
export function isDroppedCapacityRead(slug: string, issuedSeq: number): boolean {
  const ledger = capacityLedger(slug);
  return (
    issuedSeq < ledger.highestPublishedReadSeq || issuedSeq < ledger.baseAcceptedSeq
  );
}

/** Record a read that is allowed to publish. Advances the receipt (S5-R5). */
export function publishCapacityRead(
  slug: string,
  issuedSeq: number,
  settledSeq: number,
  snapshot: unknown,
  now: number,
): void {
  const ledger = capacityLedger(slug);
  const classified = classifyCapacitySnapshot(snapshot);
  ledger.highestPublishedReadSeq = issuedSeq;
  ledger.observation = {
    issuedSeq,
    settledSeq,
    origin: 'read',
    // R7: an observation is `usable` only when the SAME capacity-local semantic
    // classifier the view applies accepts it. A representation/shape/domain
    // defect — or an internally inconsistent topology — is observed and
    // reported, never published as usable data.
    outcome: classified.status === 'usable' ? 'usable' : 'unusable',
    // A genuine successful network response always advances the receipt, even
    // when the payload is byte-identical to the previous one (S5-R5).
    receiptAt: now,
    sourceRevision: classified.status === 'usable' ? classified.snapshot.revision : null,
  };
}

/**
 * Record a failed read that is allowed to publish its failure. The receipt is
 * carried forward unchanged — a failure is not a genuine successful response
 * (S2), so it must not present the last accepted snapshot as re-confirmed.
 */
export function publishCapacityReadFailure(
  slug: string,
  issuedSeq: number,
  settledSeq: number,
): void {
  const ledger = capacityLedger(slug);
  ledger.highestPublishedReadSeq = issuedSeq;
  ledger.observation = {
    issuedSeq,
    settledSeq,
    origin: 'read',
    outcome: 'failed',
    receiptAt: ledger.observation?.receiptAt ?? null,
    sourceRevision: null,
  };
}

/**
 * Accept a usable write result as the new base. Keys on the write's
 * SETTLEMENT seq, not its issue seq: a read issued *during* the write carries a
 * higher issue seq, would survive an issue-keyed filter, and would republish
 * stale data over the accepted snapshot.
 */
export function acceptCapacityWrite(
  slug: string,
  settledSeq: number,
  snapshot: DaemonCapacitySnapshot,
  now: number,
): void {
  const ledger = capacityLedger(slug);
  ledger.baseAcceptedSeq = settledSeq;
  ledger.observation = {
    issuedSeq: settledSeq,
    settledSeq,
    origin: 'write',
    outcome: 'usable',
    receiptAt: now,
    sourceRevision: snapshot.revision,
  };
}

/**
 * Record a write that SETTLED but whose body is not a usable capacity snapshot.
 *
 * The write still fences later reads (its settlement is real), but it never
 * becomes an accepted observation, never advances the receipt and never reaches
 * the cache — the caller classifies it as an unknown outcome (R7 / case 15.5).
 */
export function recordUnusableCapacityWrite(slug: string, settledSeq: number): void {
  const ledger = capacityLedger(slug);
  ledger.baseAcceptedSeq = settledSeq;
  ledger.observation = {
    issuedSeq: settledSeq,
    settledSeq,
    origin: 'write',
    outcome: 'unusable',
    receiptAt: ledger.observation?.receiptAt ?? null,
    sourceRevision: null,
  };
}

/**
 * Settlements keyed by the exact request object that produced them. Weakly
 * held: a record lives exactly as long as the editor keeps its request.
 */
const writeSettlements = new WeakMap<object, CapacityWriteSettlement>();

/**
 * Record which settlement `request` produced. Called BEFORE the cache write, so
 * by the time any observer can render that settlement its owner is known.
 */
export function recordCapacityWriteSettlement(
  request: object,
  settlement: CapacityWriteSettlement,
): void {
  writeSettlements.set(request, settlement);
}

export function capacityWriteSettlement(request: object): CapacityWriteSettlement | null {
  return writeSettlements.get(request) ?? null;
}

export function capacityObservation(slug: string): CapacityObservation | null {
  return capacityLedger(slug).observation;
}

/** Test-only reset so each mounted case starts from a clean ordering ledger. */
export function resetCapacityOrdering(): void {
  ledgers.clear();
  seqCounter = 0;
}

// ---------------------------------------------------------------------------
// Capacity semantic / representation classification
// ---------------------------------------------------------------------------
//
// R7 repair: the provider used to decide "is this a capacity snapshot?" with a
// weak container-shape predicate while the view applied a much stronger
// semantic guard. A PUT body carrying a raw unsafe integer therefore passed the
// provider, entered the capacity cache and published a `usable` observation
// while the view reported the result unknown. There is now ONE capacity-local
// classifier, and both the provider and the view consult it, so "usable" means
// the same thing at every entry point (accepted case 15.5).
//
// This lives beside the ordering ledger rather than in the feature module so
// the provider can import it without a features -> providers dependency; the
// feature model re-exports it under its established name.

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

/** A consumed numeric is usable only if it survived JSON.parse as a safe integer. */
export function isSafeCapacityNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value);
}

function isNullableSafe(value: unknown): boolean {
  return value === null || isSafeCapacityNumber(value);
}

export type CapacityClassification =
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
export function classifyCapacitySnapshot(raw: unknown): CapacityClassification {
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

/**
 * The single provider-side acceptance predicate. Only a fully classified
 * `usable` snapshot may become an accepted observation or enter the capacity
 * cache; `inconsistent` and `unusable` results are observed and reported, never
 * accepted (R7 / accepted case 15.5).
 */
export function isUsableCapacitySnapshot(
  value: unknown,
): value is DaemonCapacitySnapshot {
  return classifyCapacitySnapshot(value).status === 'usable';
}

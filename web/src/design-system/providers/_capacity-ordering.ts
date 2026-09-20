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
  ledger.highestPublishedReadSeq = issuedSeq;
  ledger.observation = {
    issuedSeq,
    settledSeq,
    outcome: hasCapacitySnapshotShape(snapshot) ? 'usable' : 'unusable',
    // A genuine successful network response always advances the receipt, even
    // when the payload is byte-identical to the previous one (S5-R5).
    receiptAt: now,
    sourceRevision: hasCapacitySnapshotShape(snapshot) ? snapshot.revision : null,
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
    outcome: 'usable',
    receiptAt: now,
    sourceRevision: snapshot.revision,
  };
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
// Transport-shape guard
// ---------------------------------------------------------------------------

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Minimal structural check used to classify an observation's outcome. Semantic
 * guards (numeric representation, domain and relational invariants) live with
 * the capacity view in `features/settings/sections/capacityModel.ts`; this one
 * only answers "did the transport hand us a capacity snapshot at all?".
 */
export function hasCapacitySnapshotShape(
  value: unknown,
): value is DaemonCapacitySnapshot {
  if (!isObject(value)) return false;
  return typeof value.revision === 'string' && value.revision.length > 0
    && isObject(value.persisted_yaml)
    && isObject(value.next_start)
    && isObject(value.running_at_daemon_start)
    && isObject(value.producer_components)
    && isObject(value.guidance)
    && Array.isArray(value.warnings)
    && Array.isArray(value.environment_shadowed);
}

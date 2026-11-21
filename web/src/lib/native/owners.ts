/**
 * Web owner summary push to native (THR-387 S6-W2b; bridge spec "v2 owner
 * summary and native lifecycle gate", schema `#/$defs/ownersChanged` and
 * `#/$defs/lifecycleRequested`).
 *
 * - `app.ownersChanged`: the document's ALL-owner aggregate (Main + Settings,
 *   detached owners included; no org/page filter) as the closed v2 summary
 *   `{schema:2, blocking, counts}`. Native's `"{}"` reply is a RECEIPT only.
 *   Calls are serialized in observation order (one in flight) and deduped
 *   against the last ACKNOWLEDGED summary; a failed push clears it, so the
 *   next identical summary is sent again. There is no timer retry.
 * - Save admission (`awaitOwnersReceipt`): a registered save may be sent only
 *   after native acknowledged the summary that shows it pending. A refused,
 *   failed or lost push (or no reply within `RECEIPT_TIMEOUT_MS`) holds the
 *   save: the caller settles it `failed` without sending anything.
 * - `app.lifecycleRequested` (`subscribeLifecycle`): strictly validated; an
 *   invalid event is ignored. Each request ID is answered at most once, with
 *   `gate:{requestId, decision}` on the same `app.ownersChanged` call. Proceed
 *   is sent only while the current summary is `none` (otherwise keep). From a
 *   proceed answer no new save is admitted (MM-4 minimal rule); only a
 *   definitive refusal (`effect:"none"`) restores saving, a lost
 *   acknowledgement keeps the hold.
 *
 * Browser mode (no bridge): every export is a no-op and saves are admitted at
 * once. Payloads carry no page, org, computer, owner id, draft or error text
 * and are never logged. State is module memory of this document only: a reload
 * starts empty and sends its own initial summary; nothing is replayed.
 */
import { selectOwnerBlocking, type BlockingLevel, type OwnerRecord } from '../owners/ownerRegistry';
import { nativeBridge, type NativeBridge } from './target';

export const OWNERS_COMMAND = 'app.ownersChanged';
export const LIFECYCLE_EVENT = 'app.lifecycleRequested';
export const RECEIPT_TIMEOUT_MS = 5000;
const MAX_COUNT = 255;
const REQUEST_ID = /^[A-Za-z0-9_-]{1,64}$/;

export type LifecycleAction = 'close' | 'quit' | 'relaunch';
export type GateDecision = 'keep' | 'proceed';

export interface OwnersCounts {
  dirty: number;
  failed: number;
  pending: number;
  unknown: number;
}

export interface OwnersSummary {
  schema: 2;
  blocking: BlockingLevel;
  counts: OwnersCounts;
  gate?: { requestId: string; decision: GateDecision };
}

export interface LifecycleRequest {
  requestId: string;
  action: LifecycleAction;
}

/** `acked`: native replied `"{}"`; `refused`: a definitive no-effect rejection; `lost`: anything else. */
export type PushOutcome = 'acked' | 'refused' | 'lost';

/** A save held because native did not acknowledge its pending summary. Nothing was sent. */
export class OwnersReceiptError extends Error {
  public override readonly name = 'OwnersReceiptError';
  constructor() {
    // Empty message: callers that surface raw error text fall back to their
    // localized generic copy.
    super('');
  }
}

/** The closed v2 summary of every registered owner (counts saturate at 255). */
export function toOwnersSummary(records: readonly OwnerRecord[]): OwnersSummary {
  const counts: OwnersCounts = { dirty: 0, failed: 0, pending: 0, unknown: 0 };
  for (const record of records) {
    const status = record.status;
    if (status === 'dirty' || status === 'failed' || status === 'pending' || status === 'unknown') {
      counts[status] = Math.min(MAX_COUNT, counts[status] + 1);
    }
  }
  return { schema: 2, blocking: selectOwnerBlocking(records).level, counts };
}

/** Canonical JSON (fixed key order) for the call data and dedupe. */
export function serializeOwnersSummary(summary: OwnersSummary): string {
  const { dirty, failed, pending, unknown } = summary.counts;
  const base = `{"schema":2,"blocking":${JSON.stringify(summary.blocking)},"counts":{"dirty":${dirty},"failed":${failed},"pending":${pending},"unknown":${unknown}}`;
  if (!summary.gate) return `${base}}`;
  return `${base},"gate":{"requestId":${JSON.stringify(summary.gate.requestId)},"decision":${JSON.stringify(summary.gate.decision)}}}`;
}

/** Strict `lifecycleRequested` parser: exact keys, schema 2, ID pattern, action enum. */
export function parseLifecycleRequested(raw: unknown): LifecycleRequest | null {
  if (typeof raw !== 'string' || raw.length > 1024) return null;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj).sort();
  if (keys.length !== 3 || keys[0] !== 'action' || keys[1] !== 'requestId' || keys[2] !== 'schema') return null;
  if (obj.schema !== 2) return null;
  if (typeof obj.requestId !== 'string' || !REQUEST_ID.test(obj.requestId)) return null;
  if (obj.action !== 'close' && obj.action !== 'quit' && obj.action !== 'relaunch') return null;
  return { requestId: obj.requestId, action: obj.action };
}

// ---- Document-scoped publisher state ----

let chain: Promise<unknown> = Promise.resolve();
let lastAcked: string | null = null;
let lastQueued: { key: string; result: Promise<PushOutcome>; settled: boolean } | null = null;
let frozen = false;
/** Target-switch/disconnect save holds (S6-W3): no save is admitted while any is held. */
let switchHolds = 0;
const seenRequests = new Set<string>();
const answeredRequests = new Set<string>();

function isDefinitiveRefusal(error: unknown): boolean {
  if (typeof error !== 'string') return false;
  try {
    const body = JSON.parse(error) as { code?: unknown; effect?: unknown } | null;
    return !!body && typeof body.code === 'string' && body.effect === 'none';
  } catch {
    return false;
  }
}

function callOnce(native: NativeBridge, data: string): Promise<PushOutcome> {
  return new Promise<PushOutcome>((resolve) => {
    let done = false;
    const finish = (outcome: PushOutcome): void => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve(outcome);
    };
    // No reply in time is a lost acknowledgement (never proof of anything).
    const timer = setTimeout(() => finish('lost'), RECEIPT_TIMEOUT_MS);
    try {
      native.call(OWNERS_COMMAND, data).then(
        (reply) => finish(reply === '{}' ? 'acked' : 'lost'),
        (error: unknown) => finish(isDefinitiveRefusal(error) ? 'refused' : 'lost'),
      );
    } catch (error) {
      finish(isDefinitiveRefusal(error) ? 'refused' : 'lost');
    }
  });
}

/** Queue one call after every earlier one (observation order, one in flight). */
function enqueue(native: NativeBridge, build: () => { key: string; data: string } | null): Promise<PushOutcome> {
  const result = chain.then(async (): Promise<PushOutcome> => {
    const next = build();
    if (next === null) return 'acked';
    const outcome = await callOnce(native, next.data);
    lastAcked = outcome === 'acked' ? next.key : null;
    return outcome;
  });
  chain = result.catch(() => undefined);
  return result;
}

/**
 * Push the summary of `records` (as observed now). Resolves with the push
 * outcome; an identical summary already acknowledged is not re-sent.
 */
export function publishOwners(records: readonly OwnerRecord[]): Promise<PushOutcome> {
  const native = nativeBridge();
  if (!native) return Promise.resolve('acked');
  const key = serializeOwnersSummary(toOwnersSummary(records));
  const entry = {
    key,
    settled: false,
    result: enqueue(native, () => (key === lastAcked ? null : { key, data: key })),
  };
  lastQueued = entry;
  void entry.result.then(() => {
    entry.settled = true;
  });
  return entry.result;
}

/**
 * Save admission: resolves true once native acknowledged the summary of the
 * current `records` (the one showing this save pending). False holds the save:
 * nothing may be sent. Browser mode: true at once.
 */
export async function awaitOwnersReceipt(records: readonly OwnerRecord[]): Promise<boolean> {
  const native = nativeBridge();
  if (!native) return true;
  if (frozen || switchHolds > 0) return false;
  const key = serializeOwnersSummary(toOwnersSummary(records));
  // Share the push already queued for this exact summary (no second call).
  const queued = lastQueued && lastQueued.key === key && !lastQueued.settled ? lastQueued.result : null;
  const outcome = queued ? await queued : await publishOwners(records);
  return outcome === 'acked' && !frozen && switchHolds === 0;
}

/**
 * True after a proceed answer until a definitive refusal, and while a
 * target switch/disconnect holds saves (no save is admitted).
 */
export function ownersFrozen(): boolean {
  return frozen || switchHolds > 0;
}

/**
 * Freeze saves for a native target commit (`client.commitSwitch` /
 * `client.disconnect`, S6-W3; bridge spec "web pauses affected mutations for
 * the final switch"). While held, `awaitOwnersReceipt` admits no save, so the
 * acknowledged summary native gates on cannot change under the commit.
 * Returns an idempotent release; the caller releases when native answers
 * (refusal or success). Browser mode: a no-op hold.
 */
export function holdOwnerSaves(): () => void {
  switchHolds += 1;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    switchHolds -= 1;
  };
}

/**
 * Answer a lifecycle request once, on the same `app.ownersChanged` call. The
 * summary (and the proceed check) is read when the call is actually sent.
 */
export function answerLifecycle(
  requestId: string,
  decision: GateDecision,
  read: () => readonly OwnerRecord[],
): Promise<PushOutcome> | null {
  const native = nativeBridge();
  if (!native || answeredRequests.has(requestId)) return null;
  answeredRequests.add(requestId);
  // Final recheck: from a proceed answer no new save is admitted.
  if (decision === 'proceed') frozen = true;
  let sent: GateDecision = decision;
  const result = enqueue(native, () => {
    const summary = toOwnersSummary(read());
    if (sent === 'proceed' && summary.blocking !== 'none') {
      sent = 'keep';
      frozen = false;
    }
    return {
      key: serializeOwnersSummary(summary),
      data: serializeOwnersSummary({ ...summary, gate: { requestId, decision: sent } }),
    };
  });
  if (decision === 'proceed') {
    void result.then((outcome) => {
      // A definitive refusal restores saving; a lost acknowledgement keeps the hold.
      if (sent === 'proceed' && outcome === 'refused') frozen = false;
    });
  }
  return result;
}

/**
 * Subscribe to `app.lifecycleRequested`. Invalid events and IDs already
 * answered are ignored; a repeat of an unanswered ID is delivered with
 * `repeat: true` (focus the open gate only). A bridge without the event (its
 * `on` throws) is tolerated silently.
 */
export function subscribeLifecycle(handler: (request: LifecycleRequest & { repeat: boolean }) => void): () => void {
  const native = nativeBridge();
  if (!native) return () => undefined;
  let off: unknown;
  try {
    off = native.on(LIFECYCLE_EVENT, (raw) => {
      const request = parseLifecycleRequested(raw);
      if (!request || answeredRequests.has(request.requestId)) return;
      const repeat = seenRequests.has(request.requestId);
      seenRequests.add(request.requestId);
      handler({ ...request, repeat });
    });
  } catch {
    return () => undefined;
  }
  return () => {
    if (typeof off !== 'function') return;
    try {
      (off as () => void)();
    } catch {
      // Already retired subscription.
    }
  };
}

/** Test-only (web/scripts/native-owners.test.mjs): reset this module's state. */
export function __resetNativeOwnersForTests(): void {
  chain = Promise.resolve();
  lastAcked = null;
  lastQueued = null;
  frozen = false;
  switchHolds = 0;
  seenRequests.clear();
  answeredRequests.clear();
}

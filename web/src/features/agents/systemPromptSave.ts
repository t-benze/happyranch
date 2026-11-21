/**
 * System prompt save + verification read (THR-387 S6-W2a). Pure: no React,
 * no imports with runtime effect, so `web/scripts/system-prompt-save.test.mjs`
 * runs it against the real owner registry under plain Node.
 *
 * Settlement rule: the owner ticket settles `saved` ONLY when the PUT was
 * acknowledged AND a verification read matches the receipt. An acknowledged
 * PUT whose verification read fails (e.g. 503) or disagrees (another prompt
 * or revision) settles `unknown`: the owner stays held (non-discardable, new
 * saves refused, org/target switch and Quit blocked) until a MATCHING read
 * (Check result or Reload) resolves it. It never settles `saved` first and
 * corrects itself later, so a queued transition cannot observe a transient
 * clean state.
 *
 * Explicit reads (`reconcileSystemPromptRead`) keep the two unknown kinds
 * apart: for an ACKNOWLEDGED-but-unverified save a failed or disagreeing read
 * stays `unknown` (a different current value cannot prove an acknowledged
 * write failed; another writer may have replaced it). Only a NEVER-acknowledged
 * (lost) PUT may reconcile to `not_saved` on a disagreeing read.
 */
import type { SaveOutcome } from '@/lib/owners/ownerRegistry';

export interface PromptReceipt {
  agent: string;
  system_prompt: string;
  revision: string;
}

export interface PromptRead {
  system_prompt: string;
  /** Validated by the caller's `validRevision`; the roster read may omit it. */
  revision?: string | null;
}

export type PromptSaveResult<R extends PromptRead> =
  /** Acknowledged and confirmed by a matching read. */
  | { kind: 'verified'; fresh: R }
  /** Acknowledged, but the verification read was empty or disagreed. */
  | { kind: 'unverified'; fresh: R | null | undefined }
  /** The PUT failed, or the verification read failed. */
  | { kind: 'error'; error: unknown };

/** The sent text plus, once the PUT was acknowledged, its receipt. */
export interface PromptSubmission {
  system_prompt: string;
  /** Non-null exactly when the PUT was acknowledged. */
  acknowledged: PromptReceipt | null;
}

/**
 * Outcome of an explicit read (Check result / Reload) for an unknown save.
 * - Never acknowledged (lost PUT): stored == sent text -> saved, otherwise
 *   not_saved (unchanged reconciliation; the revision guard refuses a stale
 *   write, so the draft is kept as unsaved for an explicit retry).
 * - Acknowledged but unverified: ONLY stored == sent text with the receipt's
 *   revision resolves it (saved); any other read stays unknown.
 * - No read or no submission: unknown.
 */
export function reconcileSystemPromptRead(
  sent: PromptSubmission | null,
  fresh: PromptRead | null | undefined,
): 'saved' | 'not_saved' | 'unknown' {
  if (!fresh || !sent) return 'unknown';
  const sameText = fresh.system_prompt === sent.system_prompt;
  if (sent.acknowledged === null) return sameText ? 'saved' : 'not_saved';
  return sameText && fresh.revision === sent.acknowledged.revision ? 'saved' : 'unknown';
}

export async function saveAndVerifySystemPrompt<R extends PromptRead>(deps: {
  agentName: string;
  save: () => Promise<PromptReceipt>;
  read: () => Promise<R | null | undefined>;
  classifySaveError: (error: unknown) => 'failed' | 'unknown';
  validRevision: (value: unknown) => boolean;
  /** Called as soon as the PUT is acknowledged, before the verification read. */
  onAcknowledged?: (receipt: PromptReceipt) => void;
}): Promise<{ outcome: SaveOutcome; result: PromptSaveResult<R> }> {
  let receipt: PromptReceipt;
  try {
    receipt = await deps.save();
  } catch (error: unknown) {
    return { outcome: deps.classifySaveError(error), result: { kind: 'error', error } };
  }
  deps.onAcknowledged?.(receipt);
  // Acknowledged, not yet verified: unknown until a matching read.
  let fresh: R | null | undefined;
  try {
    fresh = await deps.read();
  } catch (error: unknown) {
    return { outcome: 'unknown', result: { kind: 'error', error } };
  }
  if (!fresh || receipt.agent !== deps.agentName || !deps.validRevision(receipt.revision)
      || fresh.system_prompt !== receipt.system_prompt || fresh.revision !== receipt.revision) {
    return { outcome: 'unknown', result: { kind: 'unverified', fresh } };
  }
  return { outcome: 'saved', result: { kind: 'verified', fresh } };
}

/**
 * System prompt save + verification read (THR-387 S6-W2a). Pure: no React,
 * no imports with runtime effect, so `web/scripts/system-prompt-save.test.mjs`
 * runs it against the real owner registry under plain Node.
 *
 * Settlement rule: the owner ticket settles `saved` ONLY when the PUT was
 * acknowledged AND a verification read matches the receipt. An acknowledged
 * PUT whose verification read fails (e.g. 503) or disagrees (another prompt
 * or revision) settles `unknown`: the owner stays held (non-discardable, new
 * saves refused, org/target switch and Quit blocked) until Check result or
 * Reload reconciles it. It never settles `saved` first and corrects itself
 * later, so a queued transition cannot observe a transient clean state.
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

export async function saveAndVerifySystemPrompt<R extends PromptRead>(deps: {
  agentName: string;
  save: () => Promise<PromptReceipt>;
  read: () => Promise<R | null | undefined>;
  classifySaveError: (error: unknown) => 'failed' | 'unknown';
  validRevision: (value: unknown) => boolean;
}): Promise<{ outcome: SaveOutcome; result: PromptSaveResult<R> }> {
  let receipt: PromptReceipt;
  try {
    receipt = await deps.save();
  } catch (error: unknown) {
    return { outcome: deps.classifySaveError(error), result: { kind: 'error', error } };
  }
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

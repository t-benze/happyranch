/**
 * Pure presentation helpers for the "Top token threads (window)" panel
 * (token-usage visibility surface, THR-015 Track B, spec §2/§5/§6).
 *
 * Model classification lives in the neutral `@/lib/modelClassification`
 * helper so Dashboard and Usage share the CLI-mirrored label precedence while
 * this feature module retains only dashboard ranking/presentation.
 *
 * Churn invariant (non-negotiable): a row's churn is `total_tokens`
 * (= input + output + reasoning). `cache_read_tokens` rides along as a muted
 * secondary number and is NEVER summed into the total, nor used as a sort,
 * rank, or threshold key.
 */
import {
  classifyModel,
  type ModelClassificationRow,
} from '@/lib/modelClassification';

/** The subset of a `TokenUsageRollup` row this layer reads. Structural so a
 *  full thread rollup row (which has every field) is assignable to it. */
export interface RollupRow extends ModelClassificationRow {
  thread_id?: string | null;
  sessions: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
}

/**
 * Sentinel thread identity for a rollup row with no thread. Kept verbatim as
 * the machine key (sort tie-break / React key / CLI parity); the panel maps it
 * to a localized display label at render time.
 */
export const NO_THREAD_ID = '(no thread)';

/** What the panel renders per row. */
export interface TopTokenRow {
  threadId: string;
  modelLabel: string;
  sessions: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number; // shown, secondary, NEVER summed into total
  totalTokens: number; // = input + output + reasoning; the bar length / sort key
}

/**
 * Rank a thread rollup by churn DESC and slice to the top N.
 *
 * Sort key is `total_tokens` ONLY (the churn invariant). Ties break by
 * `sessions` DESC then thread id ASC for stable output, mirroring the CLI's
 * `--top` ordering.
 */
export function toTopRows(rollup: RollupRow[], topN: number): TopTokenRow[] {
  return rollup
    .map((r) => ({
      threadId: r.thread_id ?? NO_THREAD_ID,
      modelLabel: classifyModel(r),
      sessions: r.sessions,
      inputTokens: r.input_tokens,
      outputTokens: r.output_tokens,
      cacheReadTokens: r.cache_read_tokens,
      totalTokens: r.total_tokens,
    }))
    .sort(
      (a, b) =>
        b.totalTokens - a.totalTokens ||
        b.sessions - a.sessions ||
        a.threadId.localeCompare(b.threadId),
    )
    .slice(0, topN);
}

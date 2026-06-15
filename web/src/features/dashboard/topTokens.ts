/**
 * Pure presentation helpers for the "Top token threads (window)" panel
 * (token-usage visibility surface, THR-015 Track B, spec §2/§5/§6).
 *
 * This is the WEB mirror of the CLI's `classify_model` (cli/commands/tasks.py,
 * Leg B). The Model label, the cutover constant, the EM-DASH wording, and the
 * Date-not-string boundary compare are all kept identical so the dashboard and
 * the CLI never disagree about what a row's model is.
 *
 * Churn invariant (non-negotiable): a row's churn is `total_tokens`
 * (= input + output + reasoning). `cache_read_tokens` rides along as a muted
 * secondary number and is NEVER summed into the total, nor used as a sort,
 * rank, or threshold key.
 */

// The cutover that separates frozen pre-fix history from the model-population
// fix (Track A, PR #83 / merge 3292962). A SINGLE web presentation constant —
// it only re-labels NULL-model rows at render time (O2), never a schema value.
// Mirrors MODEL_FIX_CUTOVER_TS in cli/commands/tasks.py; trivially changeable.
export const MODEL_FIX_CUTOVER_TS = '2026-06-12T15:38:50Z';

/** The subset of a `TokenUsageRollup` row this layer reads. Structural so a
 *  full thread rollup row (which has every field) is assignable to it. */
export interface RollupRow {
  thread_id?: string | null;
  sessions: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  model_distinct?: number;
  model_any?: string | null;
  non_null_sessions?: number;
  null_codex_sessions?: number;
  null_claude_sessions?: number;
  null_claude_max_created_at?: string | null;
}

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
 * Parse an ISO-8601 timestamp to epoch millis for the cutover compare.
 *
 * The DB stamps `created_at` as `...+00:00`; `MODEL_FIX_CUTOVER_TS` uses `Z`.
 * A lexicographic compare ('+' < 'Z') would mislabel a same-instant row as
 * pre-fix and hide the parser-drift canary (LRN-035). JS `Date` parses both
 * suffixes, so compare on the parsed value, never the string.
 */
function parseTs(value: string): number {
  return new Date(value).getTime();
}

/**
 * Render the Model label for a by-thread (or by-agent) rollup row.
 *
 * Mirrors `classify_model` precedence (spec §2/§6). Token totals stay
 * authoritative regardless of the label.
 */
export function classifyModel(row: RollupRow): string {
  const modelDistinct = row.model_distinct ?? 0;
  const nonNull = row.non_null_sessions ?? 0;
  const nullCodex = row.null_codex_sessions ?? 0;
  const nullClaude = row.null_claude_sessions ?? 0;
  const nullPresent = nullCodex + nullClaude > 0;

  if (nonNull > 0) {
    // one or more observed (non-NULL) models on this rollup
    if (modelDistinct > 1 || nullPresent) return '(mixed)';
    return row.model_any || '(mixed)';
  }

  // every session on this rollup has a NULL model
  if (nullCodex > 0 && nullClaude > 0) return '(mixed)'; // all-NULL spanning codex + claude
  if (nullCodex > 0) return '(cli-unreported)'; // codex emits no model field, ever (O1)
  if (nullClaude > 0) {
    // claude NULLs split on the cutover: frozen pre-fix history vs a post-fix
    // anomaly worth investigating (parser-drift canary, §2/§6).
    const maxTs = row.null_claude_max_created_at;
    if (maxTs != null && parseTs(maxTs) >= parseTs(MODEL_FIX_CUTOVER_TS)) {
      return '(unknown — ANOMALY)';
    }
    return '(unknown — pre-fix)';
  }
  return '(unknown)'; // no sessions at all (defensive)
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
      threadId: r.thread_id ?? '(no thread)',
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

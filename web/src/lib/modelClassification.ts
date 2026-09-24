/**
 * Feature-neutral token-rollup model classification.
 *
 * Mirrors the CLI's `classify_model` precedence in `cli/commands/tasks.py` so
 * every web consumer renders the same labels without importing another
 * feature domain.
 */

// Separates frozen pre-fix history from the model-population fix (Track A,
// PR #83 / merge 3292962). This is presentation-only and never a schema value.
export const MODEL_FIX_CUTOVER_TS = '2026-06-12T15:38:50Z';

/** Structural subset shared by thread and agent token rollups. */
export interface ModelClassificationRow {
  model_distinct?: number;
  model_any?: string | null;
  non_null_sessions?: number;
  null_codex_sessions?: number;
  null_claude_sessions?: number;
  null_claude_max_created_at?: string | null;
}

/** Compare parsed instants: DB `+00:00` and constant `Z` forms are equivalent. */
function parseTs(value: string): number {
  return new Date(value).getTime();
}

/** Render the canonical model label for a token-usage rollup row. */
export function classifyModel(row: ModelClassificationRow): string {
  const modelDistinct = row.model_distinct ?? 0;
  const nonNull = row.non_null_sessions ?? 0;
  const nullCodex = row.null_codex_sessions ?? 0;
  const nullClaude = row.null_claude_sessions ?? 0;
  const nullPresent = nullCodex + nullClaude > 0;

  if (nonNull > 0) {
    if (modelDistinct > 1 || nullPresent) return '(mixed)';
    return row.model_any || '(mixed)';
  }

  if (nullCodex > 0 && nullClaude > 0) return '(mixed)';
  if (nullCodex > 0) return '(cli-unreported)';
  if (nullClaude > 0) {
    const maxTs = row.null_claude_max_created_at;
    if (maxTs != null && parseTs(maxTs) >= parseTs(MODEL_FIX_CUTOVER_TS)) {
      return '(unknown — ANOMALY)';
    }
    return '(unknown — pre-fix)';
  }
  return '(unknown)';
}

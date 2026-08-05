/**
 * Runtime count validation for Dreams rendering.
 *
 * DreamRecord declares counts as `number`, but malformed server payloads may
 * arrive missing, non-numeric, non-finite, or negative. These helpers live at
 * the rendering boundary so the TypeScript contract stays strict and the UI
 * renders unavailable values truthfully instead of coercing them to factual
 * copy like "undefined learnings" or "NaN learnings".
 */

/** A count is valid for display only when it is a finite, non-negative number. */
export function isValidCount(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

/** Format a single count with an explicit unavailable fallback. */
export function formatCount(value: unknown, singular: string, plural: string): string {
  return isValidCount(value)
    ? `${value} ${value === 1 ? singular : plural}`
    : `${plural.charAt(0).toUpperCase() + plural.slice(1)} unavailable`;
}

/**
 * Format a total over multiple counts. If any value is invalid, the total is
 * unavailable rather than a partial, misleading sum.
 */
export function formatTotalCount(values: unknown[], singular: string, plural: string): string {
  const allValid = values.every(isValidCount);
  if (!allValid) return `${plural.charAt(0).toUpperCase() + plural.slice(1)} unavailable`;
  const total = values.reduce((sum, v) => sum + (v as number), 0);
  return `${total} ${total === 1 ? singular : plural}`;
}

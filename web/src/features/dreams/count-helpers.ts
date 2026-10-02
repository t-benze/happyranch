/**
 * Runtime count validation for Dreams rendering.
 *
 * DreamRecord declares counts as `number`, but malformed server payloads may
 * arrive missing, non-numeric, non-finite, or negative. These helpers live at
 * the rendering boundary so the TypeScript contract stays strict and the UI
 * renders unavailable values truthfully instead of coercing them to factual
 * copy like "undefined learnings" or "NaN learnings". Copy comes from the
 * typed catalog (THR-118 W4a-1): `countKey` is a `{count}` plural message and
 * `unavailableKey` its explicit unavailable fallback.
 */
import type { MessageKey, MessageParams } from '@/lib/i18n';

type Translate = (key: MessageKey, params?: MessageParams) => string;

/** A count is valid for display only when it is a finite, non-negative number. */
export function isValidCount(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

/** Format a single count with an explicit unavailable fallback. */
export function formatCount(
  value: unknown,
  t: Translate,
  countKey: MessageKey,
  unavailableKey: MessageKey,
): string {
  return isValidCount(value) ? t(countKey, { count: value }) : t(unavailableKey);
}

/**
 * Format a total over multiple counts. If any value is invalid, the total is
 * unavailable rather than a partial, misleading sum.
 */
export function formatTotalCount(
  values: unknown[],
  t: Translate,
  countKey: MessageKey,
  unavailableKey: MessageKey,
): string {
  if (!values.every(isValidCount)) return t(unavailableKey);
  const total = values.reduce<number>((sum, v) => sum + (v as number), 0);
  return t(countKey, { count: total });
}

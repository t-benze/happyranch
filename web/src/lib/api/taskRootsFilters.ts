/**
 * Handwritten wire types for the PR5 roots attribute filters (THR-390 PR8).
 * Filters and export only; definitions stay in `taskAttributes.ts`.
 *
 * GET /tasks/roots and GET /tasks/roots/export accept repeatable
 * `attr_min=KEY=NUMBER`, `attr_max=KEY=NUMBER`, `attr_set=KEY` and
 * `attr_not_set=KEY` (docs/specs/2026-11-25-custom-task-attributes-list-export.md).
 * Bounds are carried as the caller's exact strings, never parsed as numbers.
 */
import type { AttributePresence } from './taskAttributeTypes';

export const ATTRIBUTE_FILTER_PARAMS = ['attr_min', 'attr_max', 'attr_set', 'attr_not_set'] as const;
export type AttributeFilterParam = (typeof ATTRIBUTE_FILTER_PARAMS)[number];

/** One query occurrence, exactly as it is sent: `[name, decoded value]`. */
export type AttributeFilter = readonly [AttributeFilterParam, string];

/** Rows of a filtered roots list carry the sorted filtered keys' current values. */
export type AttributeValues = Record<string, AttributePresence>;

/** 422 `attribute_filter_*` / `task_export_invalid` and 413 `task_export_too_large` detail. */
export interface AttributeFilterErrorDetail {
  code: string;
  errors?: Record<string, string[]>;
}

/** A query string with repeatable occurrences, in the given order (`+` is sent as %2B). */
export function encodeQuery(pairs: readonly (readonly [string, string])[]): string {
  const qs = new URLSearchParams();
  for (const [name, value] of pairs) qs.append(name, value);
  return qs.toString();
}

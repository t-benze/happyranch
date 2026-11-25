/**
 * Page-level classification of the ordinary owners' errors (THR-390 PR8).
 * Priority: a filter refusal (422 `attribute_filter_*`), then access loss
 * (401 after the client's one re-send, or 403), then the existing per-owner UI.
 * The attention owner is filter-free and is never classified here.
 */
import type { TaskListStream, TaskListStreamError } from '@/hooks/tasks';
import type { MessageKey, MessageParams } from '@/lib/i18n';

export type ListErrorClass =
  | { kind: 'refused'; error: TaskListStreamError }
  | { kind: 'accessDenied' }
  | null;

export function classifyListError(owners: readonly TaskListStream[]): ListErrorClass {
  const errors = owners.flatMap((owner) => (owner.error ? [owner.error] : []));
  const refused = errors.find((error) => error.status === 422 && !!error.code?.startsWith('attribute_filter_'));
  if (refused) return { kind: 'refused', error: refused };
  if (errors.some((error) => error.status === 401 || error.status === 403)) return { kind: 'accessDenied' };
  return null;
}

const firstField = (error: TaskListStreamError): string => Object.keys(error.errors)[0] ?? '';
const firstReason = (error: TaskListStreamError): string => Object.values(error.errors)[0]?.[0] ?? (error.code ?? '');

/** The one refusal message: names the key, reason or raw code; never "no results". */
export function refusalMessage(error: TaskListStreamError): [MessageKey, MessageParams] {
  switch (error.code) {
    case 'attribute_filter_unknown_key':
      return ['tasks.filters.attr.unavailable', { key: firstField(error) }];
    case 'attribute_filter_not_numeric':
      return ['tasks.filters.attr.notNumeric', { key: firstField(error) }];
    case 'attribute_filter_inverted_range':
      return ['tasks.filters.attr.inverted', { key: firstField(error) }];
    case 'attribute_filter_invalid':
      return ['tasks.filters.attr.invalidUrl', { reason: firstReason(error) }];
    default:
      return ['tasks.filters.attr.refusedOther', { code: error.code ?? String(error.status) }];
  }
}

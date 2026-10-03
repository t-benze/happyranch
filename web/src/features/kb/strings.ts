import { formatCountFor, type Locale, type MessageKey, type MessageParams } from '@/lib/i18n';

const ERROR_KEYS: Record<string, MessageKey> = {
  empty_slug: 'kb.error.emptySlug', empty_title: 'kb.error.emptyTitle',
  empty_type: 'kb.error.emptyType', empty_topic: 'kb.error.emptyTopic',
  duplicate_slug: 'kb.error.duplicateSlug', unknown_related_entry: 'kb.error.unknownRelated',
  not_found: 'kb.error.notFound', candidate_not_found: 'kb.error.candidateNotFound',
  candidate_already_decided: 'kb.error.candidateDecided', candidate_already_promoted: 'kb.error.candidatePromoted',
};
export type KbErrorView = { kind: 'message'; key: MessageKey } | { kind: 'raw'; text: string };
/** F1: known product code, unknown code verbatim, code-less diagnostic, then fallback. */
export function classifyKbError(err: unknown, fallback: MessageKey): KbErrorView {
  const code = (err as { code?: string } | null | undefined)?.code;
  if (code) return Object.prototype.hasOwnProperty.call(ERROR_KEYS, code)
    ? { kind: 'message', key: ERROR_KEYS[code] } : { kind: 'raw', text: code };
  let text: unknown = null;
  if (typeof err === 'string') text = err;
  else if (err !== null && typeof err === 'object' && 'detail' in err) text = err.detail;
  else if (err instanceof Error) text = err.message;
  return typeof text === 'string' && text.trim() !== ''
    ? { kind: 'raw', text } : { kind: 'message', key: fallback };
}
export function renderKbError(view: KbErrorView, t: (key: MessageKey) => string): string {
  return view.kind === 'raw' ? view.text : t(view.key);
}
/** Preserve existing minute/hour/day rounding, with explicit invalid/unavailable display. */
export function relativeKbAge(iso: string, locale: Locale, t: (key: MessageKey, params?: MessageParams) => string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms)) return t('kb.age.unavailable');
  const min = Math.round(ms / 60000);
  if (min < 1) return t('kb.age.now');
  const hr = Math.round(min / 60);
  const days = Math.round(hr / 24);
  const count = min < 60 ? min : hr < 24 ? hr : days;
  const key = min < 60 ? 'kb.age.minutes' : hr < 24 ? 'kb.age.hours' : 'kb.age.days';
  return t(key, { count, number: formatCountFor(locale, count) });
}

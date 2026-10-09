/**
 * Skills presentation helpers (THR-118 W4c).
 *
 * `classifySkillError` is the same F1 diagnostic boundary as
 * `classifyWorkHoursError` / `classifyTodoError`, used for every Skills error
 * display site (catalog/detail/validation/custom load errors, the create
 * error, the eligibility/purge/assignment mutation errors). Components hold a
 * locale-neutral `SkillErrorView` and render it through `t` on every render,
 * so mapped copy re-translates in place while daemon diagnostics stay
 * byte-for-byte verbatim.
 */
import type { MessageKey, MessageParams } from '@/lib/i18n';

export type Translate = (key: MessageKey, params?: MessageParams) => string;

/** Product codes the Skills UI owns copy for. Any other code renders raw. */
const SKILL_ERROR_KEYS: Record<string, MessageKey> = {
  invalid_slug: 'skills.create.error.invalidSlug',
  slug_permanently_reserved: 'skills.create.error.reserved',
  not_found: 'skills.detail.loadErrorBody',
};

export type SkillErrorView = { kind: 'message'; key: MessageKey } | { kind: 'raw'; text: string };

/**
 *  - a recognized product code -> its catalog key;
 *  - any other non-empty code -> the raw code, verbatim;
 *  - no/empty code but a non-empty string diagnostic -> that text, verbatim
 *    (an ApiError-shaped value's string `detail`, or a plain Error's message /
 *    a thrown string; ApiError's synthetic 'API <status>' message is never one);
 *  - otherwise -> the localized `fallback`, so an error is never blank.
 */
export function classifySkillError(err: unknown, fallback: MessageKey): SkillErrorView {
  const code = (err as { code?: string } | null | undefined)?.code;
  if (code) {
    if (Object.prototype.hasOwnProperty.call(SKILL_ERROR_KEYS, code)) {
      return { kind: 'message', key: SKILL_ERROR_KEYS[code] };
    }
    return { kind: 'raw', text: code };
  }
  const diagnostic = rawDiagnostic(err);
  return diagnostic === null ? { kind: 'message', key: fallback } : { kind: 'raw', text: diagnostic };
}

function rawDiagnostic(err: unknown): string | null {
  let text: unknown = null;
  if (typeof err === 'string') text = err;
  else if (err !== null && typeof err === 'object' && 'detail' in err) text = err.detail;
  else if (err instanceof Error) text = err.message;
  return typeof text === 'string' && text.trim() !== '' ? text : null;
}

export function renderSkillError(view: SkillErrorView, t: (key: MessageKey) => string): string {
  return view.kind === 'raw' ? view.text : t(view.key);
}

/**
 * Mutation/section error copy: a mapped message renders alone; a raw daemon
 * diagnostic is appended after the localized context line (`fallback`) so the
 * operator keeps both what failed and the verbatim daemon reason.
 */
export function renderSkillErrorInContext(
  view: SkillErrorView,
  fallback: MessageKey,
  t: Translate,
): string {
  if (view.kind === 'message') return t(view.key);
  return t('skills.error.withDiagnostic', { message: t(fallback), diagnostic: view.text });
}

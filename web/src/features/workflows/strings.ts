import type { MessageKey } from '@/lib/i18n';
import { ReviewIntegrityError } from '@/lib/workflowReviewSelection';
export function reviewErrorKey(error: unknown): MessageKey {
  return error instanceof ReviewIntegrityError ? 'workflows.integrity' : 'workflows.errorBody';
}
/** Raw daemon fields are text, never translated, interpreted or rendered as HTML. */
export function reviewDiagnostic(error: unknown): string {
  const e = error as { code?: unknown; detail?: unknown } | null;
  const detail = e?.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') {
    const d = detail as Record<string, unknown>;
    return ['code','detail','owner','required_action'].map(k => typeof d[k] === 'string' ? `${k}: ${d[k]}` : '').filter(Boolean).join('\n');
  }
  return typeof e?.code === 'string' ? e.code : '';
}

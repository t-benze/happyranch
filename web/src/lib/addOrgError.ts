/**
 * Shared add-org error classification (THR-118 W2a/W2b) — `@/lib/addOrgError`.
 *
 * Extracted verbatim from `AddOrgDialog` so the onboarding create flow
 * (`CreateStep`) reuses ONE classifier and ONE renderer: the mapped daemon
 * categories stay identical, and an unrecognized non-empty daemon message is
 * retained byte-for-byte as `raw`.
 *
 * The descriptor keeps the *identity* (plus the exact submitted slug) rather
 * than a locale-rendered string, so the copy re-translates on every render and
 * follows a locale switch without resubmission (W2a review R1).
 */
import type { MessageKey, MessageParams } from '@/lib/i18n';

export type AddOrgError =
  | { kind: 'noActiveRuntime' }
  | { kind: 'dirHasData'; slug: string }
  | { kind: 'exists'; slug: string }
  | { kind: 'invalidSlug' }
  | { kind: 'generic' }
  | { kind: 'raw'; message: string };

export function classifyAddOrgError(err: unknown, submittedSlug: string): AddOrgError {
  const e = err as { code?: string; status?: number; message?: string };
  if (e.code === 'no_active_runtime') return { kind: 'noActiveRuntime' };
  if (e.code === 'org_dir_has_data') return { kind: 'dirHasData', slug: submittedSlug };
  if (e.code === 'org_exists' || e.code === 'org_dir_exists' || e.status === 409) {
    return { kind: 'exists', slug: submittedSlug };
  }
  if (e.code === 'invalid_slug') return { kind: 'invalidSlug' };
  // Preserve any exact daemon-supplied detail verbatim; only a missing message
  // falls back to the app-owned generic copy.
  const message = typeof e.message === 'string' ? e.message : '';
  if (message.trim().length > 0) return { kind: 'raw', message };
  return { kind: 'generic' };
}

export type Translator = (key: MessageKey, params?: MessageParams) => string;

export function renderAddOrgError(error: AddOrgError, t: Translator): string {
  switch (error.kind) {
    case 'noActiveRuntime':
      return t('org.add.error.noActiveRuntime');
    case 'dirHasData':
      return t('org.add.error.dirHasData', { slug: error.slug });
    case 'exists':
      return t('org.add.error.exists', { slug: error.slug });
    case 'invalidSlug':
      return t('org.add.error.invalidSlug');
    case 'generic':
      return t('org.add.error.generic');
    case 'raw':
      return error.message;
  }
}

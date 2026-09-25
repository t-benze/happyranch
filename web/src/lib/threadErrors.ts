/**
 * Pure shared error-string maps for thread operations.
 *
 * These strings were originally in features/threads/strings.ts but are
 * needed by shared modules (web/src/shared/) that may not import from
 * features/threads/. Feature folders re-export from here to keep
 * existing imports intact.
 */
import type { MessageKey, MessageParams } from '@/lib/i18n';
import { ApiError } from '@/lib/api/client';
import { MAX_THREAD_ATTACHMENTS } from './threadAttachments';

/** Daemon error-code → human-friendly message for thread operations. */
export const THREAD_ERROR_STRINGS: Record<string, string> = {
  empty_subject: 'Subject is required.',
  empty_recipients: 'At least one recipient is required.',
  empty_body: 'Body is required.',
  unknown_agent: "That agent doesn't exist in this org.",
  unknown_team: "That team doesn't exist in this org.",
  not_participant: "That agent isn't a participant in this thread.",
  thread_not_open: 'This thread is no longer open.',
  not_found: 'Thread not found.',
  invalid_token: 'Invalid invocation token (agent operation).',
  // Attachment / artifact upload + send validation codes. Before this mapping
  // the composer surfaced only the bare `HTTP <status>` fallback, so a failed
  // attachment upload could not be told apart from a failed message send.
  artifact_too_large: 'That file is too large to upload.',
  attachment_too_large: 'That file is too large to attach.',
  invalid_artifact_name: 'That file name is not allowed.',
  artifact_not_found: 'That attachment is no longer available; remove it and attach it again.',
  thread_attachment_not_found: 'That attachment is no longer available; remove it and attach it again.',
  invalid_attachment_display_name: 'That attachment name is not allowed.',
  too_many_attachments: `Too many attachments — at most ${MAX_THREAD_ATTACHMENTS} per message.`,
  duplicate_attachment: 'That attachment was added more than once.',
};

/** Map a daemon error code to a human-friendly description. */
export function describeError(code: string | null | undefined, fallback?: string): string {
  if (code && THREAD_ERROR_STRINGS[code]) return THREAD_ERROR_STRINGS[code];
  return fallback ?? code ?? 'Something went wrong.';
}

// ---------------------------------------------------------------------------
// THR-118 W3a — locale-neutral thread error descriptors.
//
// Components hold a `ThreadErrorView` in state (never a rendered string) and
// render it through `renderThreadError(view, t)` on every render, so mapped
// product copy re-translates in place on a locale switch without resubmitting.
// A daemon code with no mapping, a bare `HTTP <status>` and any non-ApiError
// diagnostic stay `raw` and are rendered byte-for-byte — even when the raw
// text is empty or happens to equal a catalog value.
// ---------------------------------------------------------------------------
/** Daemon error code → catalog key. Same code set as THREAD_ERROR_STRINGS. */
export const THREAD_ERROR_KEYS: Record<string, MessageKey> = {
  empty_subject: 'threads.error.emptySubject',
  empty_recipients: 'threads.error.emptyRecipients',
  empty_body: 'threads.error.emptyBody',
  unknown_agent: 'threads.error.unknownAgent',
  unknown_team: 'threads.error.unknownTeam',
  not_participant: 'threads.error.notParticipant',
  thread_not_open: 'threads.error.threadNotOpen',
  not_found: 'threads.error.notFound',
  invalid_token: 'threads.error.invalidToken',
  artifact_too_large: 'threads.error.artifactTooLarge',
  attachment_too_large: 'threads.error.attachmentTooLarge',
  invalid_artifact_name: 'threads.error.invalidArtifactName',
  artifact_not_found: 'threads.error.attachmentUnavailable',
  thread_attachment_not_found: 'threads.error.attachmentUnavailable',
  invalid_attachment_display_name: 'threads.error.invalidAttachmentDisplayName',
  too_many_attachments: 'threads.error.tooManyAttachments',
  duplicate_attachment: 'threads.error.duplicateAttachment',
};

export type ThreadErrorDetail =
  | { kind: 'mapped'; key: MessageKey; params?: MessageParams }
  | { kind: 'raw'; text: string };

/** Optional mapped prefix (e.g. "Upload failed for {name}: ") + detail. */
export interface ThreadErrorView {
  label?: { key: MessageKey; params?: MessageParams };
  detail: ThreadErrorDetail;
}

export type ThreadTranslator = (key: MessageKey, params?: MessageParams) => string;

/**
 * Classify a thrown value exactly like the legacy
 * `err instanceof ApiError ? describeError(err.code, `HTTP ${status}`) : String(err)`
 * expression, but as a descriptor instead of an English string.
 */
export function classifyThreadError(err: unknown): ThreadErrorDetail {
  if (err instanceof ApiError) {
    const code = err.code;
    if (code && THREAD_ERROR_KEYS[code]) {
      const key = THREAD_ERROR_KEYS[code];
      return key === 'threads.error.tooManyAttachments'
        ? { kind: 'mapped', key, params: { max: MAX_THREAD_ATTACHMENTS } }
        : { kind: 'mapped', key };
    }
    return { kind: 'raw', text: `HTTP ${err.status}` };
  }
  return { kind: 'raw', text: String(err) };
}

export function renderThreadErrorDetail(detail: ThreadErrorDetail, t: ThreadTranslator): string {
  return detail.kind === 'raw' ? detail.text : t(detail.key, detail.params);
}

export function renderThreadError(view: ThreadErrorView, t: ThreadTranslator): string {
  const body = renderThreadErrorDetail(view.detail, t);
  return view.label ? t(view.label.key, view.label.params) + body : body;
}

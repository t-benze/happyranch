/**
 * Pure shared error-string maps for thread operations.
 *
 * These strings were originally in features/threads/strings.ts but are
 * needed by shared modules (web/src/shared/) that may not import from
 * features/threads/. Feature folders re-export from here to keep
 * existing imports intact.
 */

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
};

/** Map a daemon error code to a human-friendly description. */
export function describeError(code: string | null | undefined, fallback?: string): string {
  if (code && THREAD_ERROR_STRINGS[code]) return THREAD_ERROR_STRINGS[code];
  return fallback ?? code ?? 'Something went wrong.';
}

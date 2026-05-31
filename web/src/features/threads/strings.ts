/**
 * Maps daemon error codes to human-friendly messages for the threads feature.
 * Unknown codes fall back to the raw payload in a toast.
 */
export const THREAD_ERROR_STRINGS: Record<string, string> = {
  empty_subject: 'Subject is required.',
  empty_recipients: 'At least one recipient is required.',
  empty_body: 'Body is required.',
  unknown_agent: "That agent doesn't exist in this org.",
  unknown_team: "That team doesn't exist in this org.",
  thread_not_open: 'This thread is no longer open.',
  not_found: 'Thread not found.',
  turn_cap_exceeded: 'Turn cap exceeded. Use Extend to raise it.',
  invalid_token: 'Invalid invocation token (agent operation).',
};

export function describeError(code: string | null | undefined, fallback?: string): string {
  if (code && THREAD_ERROR_STRINGS[code]) return THREAD_ERROR_STRINGS[code];
  return fallback ?? code ?? 'Something went wrong.';
}

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

/** Per §2.5.5 shared state vocabulary for the Threads surface. */
export const THREADS_STRINGS = {
  /* Page chrome — THREADS-04: serif eyebrow + title (a-threads Direction-A).
     Eyebrow segments are org-wide counts the threads-list payload already
     returns: total threads + dream-opened (composed_from_dream_id). The
     reference's "X waiting on you" segment is omitted — no awaiting-founder
     field backs it honestly. */
  pageTitle: 'Conversations across the org',
  headerEyebrow: (total: number, dreamOpened: number) =>
    `${total} THREAD${total === 1 ? '' : 'S'} · ${dreamOpened} DREAM-OPENED`,
  pageSubtitle: 'Broadcast conversations — all participants see every message.',
  newThread: '+ New',
  filterPlaceholder: 'Filter…',
  filterEmpty: 'No threads match the filter.',
  noThreads: 'No threads yet. Press N to compose.',

  /* Inbox row */
  turnBudget: (used: number, cap: number) => `${used}/${cap}`,
  nearCap: (used: number, cap: number) => `Turn budget near cap (${used}/${cap})`,

  /* Detail empty / no-messages */
  selectThread: 'Select a thread',
  selectThreadBody: 'Select a thread from the inbox, or press N to compose.',
  noMessages: 'No messages yet.',

  /* Shared state vocabulary — §2.5.5 */
  emptyTitle: 'No threads yet',
  emptyBody: 'Compose a thread to start a broadcast conversation with your agents.',
  emptyFilter: (_filter: string) => `Nothing waiting on you`,
  errorTitle: 'Couldn\'t load threads',
  errorBody: 'A backend error prevented loading threads.',
  retry: 'Retry',
  detailError: 'Failed to load thread.',
  messagesError: 'Failed to load messages.',

  /* Loading */
  loadingMessages: 'Loading messages…',

  /* Composer */
  composerHelper: 'Message the thread — all participants see it (broadcast)',
  composerPlaceholder: 'Type a message…',
  sendFailed: 'Failed to send message. Draft preserved.',

  /* Status pills */
  statusOpen: 'open',
  statusArchived: 'archived',
  statusLive: 'live',

  /* System card labels */
  systemEventLabel: 'system',

  /* Dream marker */
  dreamOriginated: 'Dream-originated thread',

  /* Detail — rail labels */
  railParticipants: 'Participants',
  railLinkedTasks: 'Linked tasks',
  railStats: 'Stats',
  railMessages: 'messages',
  railTokenChurn: 'token churn',
  railOpened: 'opened',

  /* Caps */
  capDisabledReason: 'Turn cap reached — extend to continue.',
} as const;

/**
 * Maps daemon error codes to human-friendly messages for the threads feature.
 * Re-exports from the shared lib so feature folders and shared modules
 * both import from the same source of truth.
 */
export { THREAD_ERROR_STRINGS, describeError } from '@/lib/threadErrors';

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
  newThread: '+ New thread',
  filterPlaceholder: 'Filter…',
  filterEmpty: 'No threads match the filter.',
  noThreads: 'No threads yet. Press N to compose.',

  /* Inbox row */


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

} as const;

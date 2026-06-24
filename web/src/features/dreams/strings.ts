/**
 * Dreams UI strings — kept in one file so the copypasta detective finds
 * no duplicated magic strings across the feature.
 */
export const DREAM_STRINGS = {
  /**
   * DREAMS-03 Direction-A header — uppercase eyebrow (live distinct-night
   * count derived from the loaded feed) + Newsreader serif statement title.
   * The "Next run tonight" pill from the Direction-A mock is omitted: no
   * next-run field is on the dreams payload the page loads (see completion).
   */
  headerEyebrow: (nights: number) =>
    `NIGHTLY REFLECTION · ${nights} NIGHT${nights === 1 ? '' : 'S'}`,
  headerStatement: 'Where the org slept on it.',
  emptyTitle: 'No dreams yet',
  emptyBody: 'Dreams run on the schedule configured in Settings. First reflection will appear here.',
  errorTitle: "Couldn't load dreams",
  drawerLoading: 'Loading…',
  /** Quiet-dream state — first-class positive per PRD §2.5.5 */
  quietTitle: 'Quiet dream — nothing escalated · private learning saved',
  quietBody: 'The agent completed its reflection and saved private learnings. No action needed.',
  /** Dream card status labels */
  statusCompleted: 'Completed',
  statusFailed: 'Failed',
  statusMissed: 'Missed',
  statusRunning: 'Running',
  statusTimeout: 'Timed out',
  statusSkipped: 'Skipped',
  /** Candidate review gate */
  candidatePendingLabel: (agentName: string) =>
    `from dream · proposed by ${agentName} · pending review`,
  candidateAcceptedLabel: (agentName: string) =>
    `from dream · proposed by ${agentName} · promoted to KB`,
  candidateRejectedLabel: (agentName: string) =>
    `from dream · proposed by ${agentName} · dismissed`,
  acceptButton: 'Accept',
  dismissButton: 'Dismiss',
  /** Learnings */
  learningsCount: (n: number) => `${n} learning${n === 1 ? '' : 's'}`,
  candidatesCount: (n: number) => `${n} candidate${n === 1 ? '' : 's'}`,
  reflectionsCount: (n: number) => `${n} reflection${n === 1 ? '' : 's'}`,
  /** Right-side overview rail (DREAMS-02) — data-backed totals + calm sections */
  railOverviewTitle: 'Overview',
  railCandidatesTitle: 'Knowledge candidates',
  railCandidatesEmpty: 'No knowledge candidates',
  railScheduleTitle: 'Schedule',
  /** Honest schedule note — no fabricated next-run time (none is on the payload) */
  railScheduleNote: 'Runs on the schedule configured in Settings.',
  /** Thread */
  openReflectionThread: 'Open reflection thread',
  noReflectionThread: 'No reflection thread opened',
  /** Misc */
  retry: 'Retry',
  /** Status map for display */
  statusLabel: (status: string): string => {
    const map: Record<string, string> = {
      completed: 'Completed',
      failed: 'Failed',
      missed: 'Missed',
      running: 'Running',
      timeout: 'Timed out',
      skipped: 'Skipped',
      pending: 'Pending',
    };
    return map[status] ?? status;
  },
} as const;

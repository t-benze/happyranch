export const KB_STRINGS = {
  pageTitle: 'What the org has learned',
  /** Uppercase header eyebrow — live count of rendered entries (KB-02). */
  headerEyebrow: (n: number) => `ALL ENTRIES · ${n} DOCUMENT${n === 1 ? '' : 'S'}`,
  searchPlaceholder: 'Search entries…',
  composeButton: 'Compose…',
  emptyListTitle: 'No entries yet',
  emptyListBody: 'No KB entries match the current filters. Compose one or wait for a dream to propose.',
  emptySearchTitle: 'No matches',
  emptySearchBody: 'No entries match that search.',
  drawerLoading: 'Loading entry…',
  filterAll: 'All',
  filterFolders: 'Folders',
  /** Folder-rail section headers (KB-01). */
  railSectionLibrary: 'Library',
  railSectionEngineering: 'Engineering',
  railSectionOrg: 'Org',
  /** LIBRARY overview row — selecting it clears the folder/type filter. */
  railAllEntries: 'All entries',
  authoredBy: (agent: string) => `Authored by ${agent}`,
  sourceTaskLabel: 'Source task:',
  relatedEntriesLabel: 'Related entries:',
  composeDialogTitle: 'Compose KB entry',
  composeDialogSubmit: 'Add entry',
  composeDialogSubmitting: 'Adding…',
  composeDialogCancel: 'Cancel',
  /** Candidate review gate */
  candidatePendingLabel: (agentName: string) =>
    `from dream · proposed by ${agentName} · pending review`,
  candidateAcceptedLabel: (agentName: string) =>
    `from dream · proposed by ${agentName} · promoted to KB`,
  candidateRejectedLabel: (agentName: string) =>
    `from dream · proposed by ${agentName} · dismissed`,
  acceptButton: 'Accept',
  dismissButton: 'Dismiss',
  pendingCandidatesTag: (n: number) =>
    `${n} candidate${n === 1 ? '' : 's'} pending`,
  errorLoadingCandidates: "Couldn't load candidates",
  /** Usage label — PRD §4.5 K1: viewed Nx (CLI) only */
  viewedLabel: (n: number) => `viewed ${n}× (CLI)`,
  retry: 'Retry',
};

/**
 * Maps daemon error codes to human-friendly messages for the KB feature.
 * Unknown codes fall back to the raw payload.
 */
export const KB_ERROR_STRINGS: Record<string, string> = {
  empty_slug: 'Slug is required.',
  empty_title: 'Title is required.',
  empty_type: 'Type is required.',
  empty_topic: 'Topic is required.',
  duplicate_slug: 'An entry with that slug already exists.',
  unknown_related_entry: "A related entry slug doesn't exist.",
  not_found: 'KB entry not found.',
};

export function describeError(code: string | null | undefined, fallback?: string): string {
  if (code && KB_ERROR_STRINGS[code]) return KB_ERROR_STRINGS[code];
  return fallback ?? code ?? 'Something went wrong.';
}

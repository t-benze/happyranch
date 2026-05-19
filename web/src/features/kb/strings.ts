export const KB_STRINGS = {
  pageTitle: 'Knowledge base',
  searchPlaceholder: 'Search entries…',
  composeButton: 'Compose…',
  emptyListTitle: 'No entries',
  emptyListBody: 'No KB entries match the current filters.',
  emptySearchTitle: 'No matches',
  emptySearchBody: 'No entries match that search.',
  drawerLoading: 'Loading entry…',
  filterAll: 'All',
  filterTypes: 'Types',
  filterTags: 'Tags',
  authoredBy: (agent: string) => `Authored by ${agent}`,
  sourceTaskLabel: 'Source task:',
  relatedEntriesLabel: 'Related entries:',
  composeDialogTitle: 'Compose KB entry',
  composeDialogSubmit: 'Add entry',
  composeDialogSubmitting: 'Adding…',
  composeDialogCancel: 'Cancel',
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

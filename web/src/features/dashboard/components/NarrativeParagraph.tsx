/**
 * NarrativeParagraph — render NarrativeCounts as a single calm sentence.
 *
 * Honesty principle: only counted facts. No "ran hot", no "(all on PR-review)",
 * no pattern claims.
 *
 * The escalation claim is driven by the live, routable `escalations` list
 * length passed in as `escalationCount`, not by the summary counter, so the
 * TODAY narrative never contradicts the "Waiting on you" card.
 */
import type { NarrativeCounts } from '@/lib/api/types';

interface NarrativeParagraphProps {
  counts: NarrativeCounts;
  /** Length of the rendered `escalations` list — the single source of truth
   *  for whether anything is actually waiting on the founder. */
  escalationCount: number;
}

export function NarrativeParagraph({
  counts,
  escalationCount,
}: NarrativeParagraphProps): JSX.Element {
  const {
    completed_today,
    failed_today,
    kb_added_today,
  } = counts;

  const allClear =
    completed_today === 0 && failed_today === 0 && escalationCount === 0;

  if (allClear) {
    return (
      <p className="text-text-secondary text-sm leading-relaxed">
        Quiet day. No tasks completed yet, no escalations open.
      </p>
    );
  }

  return (
    <p className="text-text-secondary text-sm leading-relaxed">
      <span className="text-text-primary font-medium">{completed_today}</span>{' '}
      tasks completed
      {failed_today > 0 && (
        <>
          {', '}
          <span className="text-tier-red font-medium">
            {failed_today} failed
          </span>
        </>
      )}
      {escalationCount > 0 && (
        <>
          {', '}
          <span className="text-tier-yellow font-medium">
            {escalationCount} {escalationCount === 1 ? 'question' : 'questions'}{' '}
            waiting on you
          </span>
        </>
      )}
      {kb_added_today > 0 && (
        <>
          {'. KB grew by '}
          <span className="text-text-primary font-medium">{kb_added_today}</span>{' '}
          {kb_added_today === 1 ? 'entry' : 'entries'}
        </>
      )}
      {'.'}
    </p>
  );
}

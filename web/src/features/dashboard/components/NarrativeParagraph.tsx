/**
 * NarrativeParagraph — render NarrativeCounts as a single calm sentence.
 *
 * Honesty principle: only counted facts. No "ran hot", no "(all on PR-review)",
 * no pattern claims.
 */
import type { NarrativeCounts } from '@/lib/api/types';

interface NarrativeParagraphProps {
  counts: NarrativeCounts;
}

export function NarrativeParagraph({
  counts,
}: NarrativeParagraphProps): JSX.Element {
  const {
    completed_today,
    failed_today,
    escalated_open,
    kb_added_today,
  } = counts;

  const allClear =
    completed_today === 0 && failed_today === 0 && escalated_open === 0;

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
      {escalated_open > 0 && (
        <>
          {', '}
          <span className="text-tier-yellow font-medium">
            {escalated_open} {escalated_open === 1 ? 'question' : 'questions'}{' '}
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

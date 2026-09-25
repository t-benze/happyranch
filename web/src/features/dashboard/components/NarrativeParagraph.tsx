/**
 * NarrativeParagraph — render NarrativeCounts as a single calm sentence.
 *
 * Honesty principle: only counted facts. No "ran hot", no "(all on PR-review)",
 * no pattern claims.
 *
 * The escalation claim is driven by the live, routable `escalations` list
 * length passed in as `escalationCount`, not by the summary counter, so the
 * TODAY narrative never contradicts the "Waiting on you" card.
 *
 * The sentence itself is built by the pure `buildNarrative` helper from the
 * active locale (THR-118 W3a), so a locale switch re-renders it in place.
 */
import type { NarrativeCounts } from '@/lib/api/types';
import { useLocale } from '@/hooks/i18n';
import { buildNarrative } from '../dashboardCopy';

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
  const locale = useLocale();
  return (
    <p className="text-text-secondary text-sm leading-relaxed">
      {buildNarrative(locale, counts, escalationCount)}
    </p>
  );
}

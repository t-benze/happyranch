/**
 * Pure, explicit-locale copy helpers for the founder dashboard (THR-118 W3a).
 *
 * No hooks: every helper takes the display `locale` and resolves catalog
 * messages through `translate`/`renderTranslated`, so callers re-render them
 * on a locale switch and unit tests can exercise both locales directly.
 * Entity values (counts, names, ids) are passed as named slot values and are
 * never rewritten.
 */
import { Fragment, type ReactNode } from 'react';
import { renderTranslated, translate, type Locale } from '@/lib/i18n';
import type { NarrativeCounts } from '@/lib/api/types';

/** Compact elapsed-time label ("5m" / "5 分钟") from a non-negative second count. */
export function formatAge(locale: Locale, seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return translate(locale, 'dashboard.age.seconds', { count: s });
  if (s < 3600) return translate(locale, 'dashboard.age.minutes', { count: Math.floor(s / 60) });
  if (s < 86400) return translate(locale, 'dashboard.age.hours', { count: Math.floor(s / 3600) });
  return translate(locale, 'dashboard.age.days', { count: Math.floor(s / 86400) });
}

/**
 * Status-summary copy for the serif greeting heading (THR-030 HOME-02).
 * Derived only from the live waiting count — never a hand-authored narrative.
 */
export function statusSummary(locale: Locale, pendingCount: number): string {
  if (pendingCount === 0) return translate(locale, 'dashboard.greeting.clear');
  return translate(locale, 'dashboard.greeting.pending', { count: pendingCount });
}

/**
 * The TODAY narrative sentence. Honesty principle: only counted facts. The
 * escalation clause follows the routable `escalationCount` (the same list the
 * "Waiting on you" card renders), never the summary counter.
 *
 * Clause order and punctuation (", " vs "，", ". " vs "。") come from the
 * catalog so each locale reads naturally; the emphasised numbers stay
 * styled spans supplied as slot values.
 */
export function buildNarrative(
  locale: Locale,
  counts: Pick<NarrativeCounts, 'completed_today' | 'failed_today' | 'kb_added_today'>,
  escalationCount: number,
): ReactNode {
  const { completed_today, failed_today, kb_added_today } = counts;

  if (completed_today === 0 && failed_today === 0 && escalationCount === 0) {
    return translate(locale, 'dashboard.narrative.quiet');
  }

  const separator = translate(locale, 'dashboard.narrative.separator');
  const parts: ReactNode[] = [
    renderTranslated(locale, 'dashboard.narrative.completed', {
      count: completed_today,
      value: <span className="text-text-primary font-medium">{completed_today}</span>,
    }),
  ];
  if (failed_today > 0) {
    parts.push(
      separator,
      <span className="text-tier-red font-medium">
        {translate(locale, 'dashboard.narrative.failed', { count: failed_today })}
      </span>,
    );
  }
  if (escalationCount > 0) {
    parts.push(
      separator,
      <span className="text-tier-yellow font-medium">
        {translate(locale, 'dashboard.narrative.questions', { count: escalationCount })}
      </span>,
    );
  }
  if (kb_added_today > 0) {
    parts.push(
      translate(locale, 'dashboard.narrative.sentenceBreak'),
      renderTranslated(locale, 'dashboard.narrative.kb', {
        count: kb_added_today,
        value: <span className="text-text-primary font-medium">{kb_added_today}</span>,
      }),
    );
  }
  parts.push(translate(locale, 'dashboard.narrative.sentenceEnd'));
  return (
    <>
      {parts.map((part, i) => (
        // Positional, fixed-order clause list — index keys are stable here.
        <Fragment key={i}>{part}</Fragment>
      ))}
    </>
  );
}

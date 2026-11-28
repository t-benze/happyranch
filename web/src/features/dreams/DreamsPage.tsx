import { IdentityName } from '@/shared/identities/IdentityName';
/**
 * DreamsPage — the reflection feed (§4.8). Direction-A Pasture fidelity
 * pass (THR-030 Leg B Batch 9).
 *
 * Layout (DREAMS-02): a single-column reflection FEED (the main column) plus
 * a right-side overview RAIL (lg:flex-row, mirroring the TASKDET-03 / JOBDET-01
 * two-column right-rail precedents). The detail drawer is a portaled overlay
 * opened from a dream card, independent of the in-flow two-column layout.
 *
 * Card routing: dream card -> dream detail drawer, .kb-cand -> KB candidate
 * detail (within the drawer), "Open reflection thread" -> the dream's thread.
 *
 * Pasture vocabulary:
 *   Feed: one List card (THR-376 PR7), one Row per dream with hairline dividers
 *   Heading: DREAMS-03 Direction-A header — uppercase eyebrow (live night
 *     count) + font-display serif (Newsreader) statement title
 *   Dream ID / timestamps / counts: font-mono tabular-nums
 *   Status pills: rounded-full bg-accent-soft/text-accent-text (completed),
 *     bg-danger-soft/text-feedback-danger (failed/timeout)
 *   Right rail: lg:w-rail (244px) overview aside
 *   Semantic text: text-text-primary / secondary / muted — no hardcoded colors
 *   Calm empty state: EmptyState with display heading
 *
 * States: Loading (skeleton cards), Empty ("No dreams yet"),
 * Quiet (positive first-class per §2.5.5), Error (retry).
 *
 * HONESTY FENCE: renders ONLY fields from DreamRecord data model
 * (dream_id, agent_name, local_date, status, summary, new_learnings_count,
 * kb_candidate_count, founder_thread_id, error). No fabricated provenance,
 * sub-states, scores, or schedule glance fields not on the model.
 */
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useDreamsList } from '@/hooks/dreams';
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap';
import { PageTitle } from '@/design-system/patterns/PageTitle';
import { List, Row } from '@/design-system/patterns/List';
import { Markdown } from '@/design-system/patterns/Markdown';
import { Button } from '@/design-system/primitives/Button';
import { CrescentMoonBadge } from '@/design-system/patterns/CrescentMoonBadge';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { cn } from '@/lib/utils';
import { SUMMARY_CODE_TOKENS } from './summary-code';
import { DreamDetailPane } from './DreamDetailPane';
import { dreamStatusLabel } from './strings';
import { isValidCount, formatCount, formatTotalCount } from './count-helpers';
import type { DreamRecord } from '@/hooks/dreams';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey, MessageParams } from '@/lib/i18n';

type Translate = (key: MessageKey, params?: MessageParams) => string;

/* ------------------------------------------------------------------ */
/*  Status pill                                                        */
/* ------------------------------------------------------------------ */

function statusPill(status: string): string {
  switch (status) {
    case 'completed': return 'bg-accent-soft text-accent-text';
    case 'failed': case 'timeout': return 'bg-danger-soft text-feedback-danger';
    case 'missed': case 'skipped': return 'bg-surface-sunken text-text-muted';
    case 'running': return 'bg-accent-soft text-accent-text';
    default: return 'bg-surface-sunken text-text-muted';
  }
}

/* ------------------------------------------------------------------ */
/*  Relative time helper                                               */
/* ------------------------------------------------------------------ */

function relativeAge(iso: string, t: Translate): string {
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.round(ms / 60000);
  if (min < 1) return t('dreams.age.justNow');
  if (min < 60) return t('dreams.age.minutes', { count: min });
  const hr = Math.round(min / 60);
  if (hr < 24) return t('dreams.age.hours', { count: hr });
  const d = Math.round(hr / 24);
  return t('dreams.age.days', { count: d });
}

/**
 * Card-preview overrides for the shared Markdown pattern. `.gl-prose`
 * (MarkdownTokens.css) is unlayered, so it beats Tailwind utilities; these
 * `!` overrides keep the preview at the card's existing italic secondary
 * 14px type with no block margins (headings included), so the two-line clamp
 * shows text, not heading chrome. Code blocks and tables are overflow-hidden so a
 * clipped code block or table is never a keyboard-focusable scroller hidden
 * under the clamp. Links stay clickable above the row's toggle overlay.
 */
const SUMMARY_PREVIEW_PROSE = cn(
  '[&_.gl-prose]:text-text-secondary! [&_.gl-prose]:text-sm!',
  '[&_.gl-prose_*]:my-0! [&_.gl-prose_:is(pre,code,table)]:overflow-hidden! [&_.gl-prose_:is(h1,h2,h3,h4,h5,h6)]:text-sm!',
  '[&_a]:relative [&_a]:z-10',
);

/* ------------------------------------------------------------------ */
/*  Dream card — Pasture card pattern                                  */
/* ------------------------------------------------------------------ */

function DreamCard({
  dream,
  slug,
  active,
  onClick,
}: {
  dream: DreamRecord;
  slug: string;
  active: boolean;
  onClick: () => void;
}): JSX.Element {
  const { t } = useTranslation();
  // Quiet-dream state is only shown when both counts are valid: a malformed
  // value must not be silently treated as "no candidates / some learnings".
  const isQuiet =
    dream.status === 'completed' &&
    isValidCount(dream.kb_candidate_count) &&
    dream.kb_candidate_count === 0 &&
    isValidCount(dream.new_learnings_count) &&
    dream.new_learnings_count > 0;

  // Dr2 (THR-376 r2): the row is a plain <li>, never a <button> wrapping
  // links. The header is the one toggle button; its ::after overlay stretches
  // over the whole row so a click anywhere still opens/closes the drawer, and
  // the focus-visible ring is drawn on that overlay so it outlines the row.
  // Real links (summary markdown links, the thread link) sit above the
  // overlay (relative z-10) and stay independently clickable and focusable.
  return (
    <Row
      padding="none"
      className={cn(
        'relative px-5 py-4 transition-colors hover:bg-surface-hover',
        active && 'ring-2 ring-inset ring-accent-soft',
      )}
    >
      {/* Header row — dream ID + agent + status pill; the row's toggle */}
      <button
        type="button"
        onClick={onClick}
        aria-pressed={active}
        className={cn(
          'mb-1.5 flex w-full items-center gap-2 text-left',
          'after:absolute after:inset-0',
          'focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-inset focus-visible:after:ring-accent-default',
        )}
      >
        <CrescentMoonBadge className="h-3.5 w-3.5" label={t('dreams.badge')} />
        <span className="text-text-primary font-mono text-xs font-medium tabular-nums">{dream.dream_id}</span>
        <span className="text-text-muted text-xs">·</span>
        <span className="text-text-secondary text-xs"><IdentityName canonicalId={dream.agent_name} /></span>
        <span className={[
          'ml-auto text-overline px-1.5 py-0.5 rounded-full font-medium uppercase tracking-wide',
          statusPill(dream.status),
        ].join(' ')}>
          {dreamStatusLabel(dream.status, t)}
        </span>
      </button>

      {/* Quote / summary — Dr1: rendered markdown (shared Markdown pattern),
          kept to the card's existing italic secondary 14px two-line preview.
          -webkit-line-clamp only counts lines inside one inline flow; block
          children (pre, headings, lists) escape it, so max-h-10 (two 20px
          text-sm lines) is the hard bound and the clamp's overflow-hidden
          clips the rest. The drawer renders the full summary. */}
      {dream.summary && (
        <div className={cn(
          'text-sm italic border-l-2 border-accent-default pl-3 mb-2',
          'text-text-secondary line-clamp-2 max-h-10',
          SUMMARY_PREVIEW_PROSE,
          SUMMARY_CODE_TOKENS,
        )}>
          <Markdown body={dream.summary} mermaidLoadingLabel={t('common.mermaidLoading')} remoteImageLabel={t('common.remoteImageBlocked')} />
        </div>
      )}

      {/* Quiet-dream state — positive first-class */}
      {isQuiet && (
        <p className="text-text-muted mb-2 text-xs italic">
          {t('dreams.quiet.title')}
        </p>
      )}

      {/* Stat strip — font-mono tabular-nums for counts */}
      <div className="text-text-muted flex items-center gap-3 font-mono text-xs tabular-nums">
        <span>{dream.local_date}</span>
        <span>·</span>
        <span>{formatCount(dream.new_learnings_count, t, 'dreams.count.learnings', 'dreams.count.learningsUnavailable')}</span>
        <span>·</span>
        <span>{formatCount(dream.kb_candidate_count, t, 'dreams.count.candidates', 'dreams.count.candidatesUnavailable')}</span>
        {dream.ended_at && (
          <>
            <span>·</span>
            <span>{relativeAge(dream.ended_at, t)}</span>
          </>
        )}
      </div>

      {/* Error indicator */}
      {dream.error && (
        <p className="text-feedback-danger mt-1 truncate font-mono text-xs">{dream.error}</p>
      )}

      {/* Open reflection thread link — a sibling of the toggle, above its overlay */}
      <div className="mt-2">
        {dream.founder_thread_id ? (
          <Link
            to={`/orgs/${slug}/threads/${dream.founder_thread_id}`}
            className="text-accent-default relative z-10 inline-block text-xs hover:underline"
          >
            {t('dreams.thread.open')} &rarr;
          </Link>
        ) : (
          <span className="text-text-muted text-xs italic">
            {t('dreams.thread.none')}
          </span>
        )}
      </div>
    </Row>
  );
}

/* ------------------------------------------------------------------ */
/*  Loading skeleton — List rows, like the feed                        */
/* ------------------------------------------------------------------ */

function LoadingSkeleton(): JSX.Element {
  const { t } = useTranslation();
  return (
    <List aria-label={t('dreams.loading')}>
      {[1, 2, 3].map((i) => (
        <Row
          key={i}
          className="animate-pulse space-y-2"
        >
          <div className="flex items-center gap-2">
            <div className="bg-surface-sunken h-3.5 w-3.5 rounded-full" />
            <div className="bg-surface-sunken h-3 w-16 rounded-sm" />
            <div className="bg-surface-sunken h-3 w-24 rounded-sm" />
          </div>
          <div className="bg-surface-sunken h-3 w-3/4 rounded-sm" />
          <div className="bg-surface-sunken h-3 w-1/2 rounded-sm" />
        </Row>
      ))}
    </List>
  );
}

/* ------------------------------------------------------------------ */
/*  Right-side overview rail (DREAMS-02)                               */
/* ------------------------------------------------------------------ */

/**
 * Honest, data-backed rail. Totals are summed from the loaded feed's
 * DreamRecord fields (reflections / learnings / candidates). The Schedule
 * section carries calm static guidance only — the next-run TIME is honestly
 * omitted because no field on the dreams payload backs it.
 */
function DreamsRail({ dreams }: { dreams: DreamRecord[] }): JSX.Element {
  const { t } = useTranslation();
  const learningValues = dreams.map((d) => d.new_learnings_count);
  const candidateValues = dreams.map((d) => d.kb_candidate_count);
  const totalLearnings = formatTotalCount(learningValues, t, 'dreams.count.learnings', 'dreams.count.learningsUnavailable');
  const totalCandidates = formatTotalCount(candidateValues, t, 'dreams.count.candidates', 'dreams.count.candidatesUnavailable');
  const allCandidatesValid = candidateValues.every(isValidCount);
  const totalCandidateNumber = allCandidatesValid
    ? candidateValues.reduce((sum, d) => sum + (d as number), 0)
    : null;

  return (
    <aside
      aria-label={t('dreams.rail.label')}
      className="lg:w-rail lg:shrink-0"
    >
      <div className="border-border-default bg-surface-raised space-y-4 rounded-md border p-4">
        {/* Overview — totals summed from the loaded feed (data-backed) */}
        <section>
          <h2 className="text-text-muted mb-2 text-xs font-semibold tracking-wider uppercase">
            {t('dreams.rail.overview')}
          </h2>
          <ul className="text-text-secondary space-y-1 font-mono text-xs tabular-nums">
            <li>{formatCount(dreams.length, t, 'dreams.count.reflections', 'dreams.count.reflectionsUnavailable')}</li>
            <li>{totalLearnings}</li>
          </ul>
        </section>

        {/* Knowledge candidates — data-backed count; calm empty when none */}
        <section className="border-border-default border-t pt-4">
          <h2 className="text-text-muted mb-2 text-xs font-semibold tracking-wider uppercase">
            {t('dreams.rail.candidates')}
          </h2>
          <p className="text-text-secondary text-xs">
            {totalCandidateNumber === null
              ? totalCandidates
              : totalCandidateNumber > 0
                ? t('dreams.count.candidates', { count: totalCandidateNumber })
                : t('dreams.rail.candidatesEmpty')}
          </p>
        </section>

        {/* Schedule — calm guidance; next-run time honestly omitted (unbacked) */}
        <section className="border-border-default border-t pt-4">
          <h2 className="text-text-muted mb-2 text-xs font-semibold tracking-wider uppercase">
            {t('dreams.rail.schedule')}
          </h2>
          <p className="text-text-muted text-xs">{t('dreams.rail.scheduleNote')}</p>
        </section>
      </div>
    </aside>
  );
}

/* ------------------------------------------------------------------ */
/*  Main page                                                          */
/* ------------------------------------------------------------------ */

export function DreamsPage(): JSX.Element {
  const { slug: orgSlug } = useParams<{ slug: string }>();
  const queryClient = useQueryClient();
  const dreamsQ = useDreamsList();
  const { t } = useTranslation();
  const [selectedDreamId, setSelectedDreamId] = useState<string | null>(null);

  const dreams = dreamsQ.data?.dreams ?? [];
  // Honest distinct-night count for the header eyebrow, derived client-side
  // from the loaded feed's local_date values (mirrors KB-02 / THREADS-04).
  const nightCount = new Set(dreams.map((d) => d.local_date)).size;

  return (
    <>
      <ContentWrap>
        {/* DREAMS-03: Direction-A eyebrow (live night count) + Newsreader serif
            statement title, mirroring the KB-02 / AUDIT-03 / THREADS-04 /
            SCHED-02 header idiom. The "Next run tonight" pill is omitted: no
            next-run field is on the dreams payload the page loads. */}
        <header className="border-border-default mb-8 border-b pb-4">
          <PageTitle
            eyebrow={t('dreams.header.eyebrow', { count: nightCount })}
            title={t('dreams.header.statement')}
          />
        </header>

        {/* Single-column reflection feed + right-side rail (DREAMS-02) */}
        <div className="flex flex-col gap-8 lg:flex-row lg:items-start">
          {/* Reflection feed — the single main column */}
          <main className="min-w-0 flex-1">
            {dreamsQ.isLoading ? (
              <LoadingSkeleton />
            ) : dreamsQ.isError ? (
              <div className="space-y-3 p-4 text-center">
                <p className="text-feedback-danger text-sm">{t('dreams.error.load')}</p>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    queryClient.invalidateQueries({
                      queryKey: ['dreams-list', orgSlug],
                    })
                  }
                >
                  {t('common.retry')}
                </Button>
              </div>
            ) : dreams.length === 0 ? (
              <EmptyState
                title={t('dreams.empty.title')}
                body={t('dreams.empty.body')}
              />
            ) : (
              <div className="flex flex-col gap-1">
                {/* Count eyebrow — Pasture label */}
                <p className="text-text-secondary mb-1 px-1 text-xs font-semibold tracking-wider uppercase">
                  {t('dreams.feed.count', { count: dreams.length })}
                </p>
                <List>
                  {dreams.map((d) => (
                    <DreamCard
                      key={d.dream_id}
                      dream={d}
                      slug={orgSlug ?? ''}
                      active={selectedDreamId === d.dream_id}
                      onClick={() =>
                        setSelectedDreamId((prev) =>
                          prev === d.dream_id ? null : d.dream_id,
                        )
                      }
                    />
                  ))}
                </List>
              </div>
            )}
          </main>

          {/* Right-side overview rail — honest, data-backed sections */}
          <DreamsRail dreams={dreams} />
        </div>
      </ContentWrap>

      {/* Detail drawer — portaled overlay, opens on dream-card click */}
      {selectedDreamId && (
        <DreamDetailPane
          dreamId={selectedDreamId}
          onClose={() => setSelectedDreamId(null)}
        />
      )}
    </>
  );
}

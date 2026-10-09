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
 *   Cards: bg-surface + border-border-default + shadow-pasture-sm + rounded-lg
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
import { Button } from '@/design-system/primitives/Button';
import { CrescentMoonBadge } from '@/design-system/patterns/CrescentMoonBadge';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { cn } from '@/lib/utils';
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

  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          'bg-surface border border-border-default shadow-pasture-sm hover:bg-surface-sunken',
          'w-full text-left p-4 rounded-lg transition-colors',
          active && 'ring-2 ring-accent-soft',
        )}
      >
        {/* Header row — dream ID + agent + status pill */}
        <div className="mb-1.5 flex items-center gap-2">
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
        </div>

        {/* Quote / summary — italic with accent left border */}
        {dream.summary && (
          <p className={cn(
            'text-sm italic border-l-2 border-accent-default pl-3 mb-2',
            'text-text-secondary line-clamp-2',
          )}>
            {dream.summary}
          </p>
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

        {/* Open reflection thread link */}
        <div className="mt-2">
          {dream.founder_thread_id ? (
            <Link
              to={`/orgs/${slug}/threads/${dream.founder_thread_id}`}
              className="text-accent-default inline-block text-xs hover:underline"
              onClick={(e) => e.stopPropagation()}
            >
              {t('dreams.thread.open')} &rarr;
            </Link>
          ) : (
            <span className="text-text-muted text-xs italic">
              {t('dreams.thread.none')}
            </span>
          )}
        </div>
      </button>
    </li>
  );
}

/* ------------------------------------------------------------------ */
/*  Loading skeleton — Pasture card-shaped                             */
/* ------------------------------------------------------------------ */

function LoadingSkeleton(): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-3 p-4" aria-label={t('dreams.loading')}>
      {[1, 2, 3].map((i) => (
        <div
          key={i}
          className="bg-surface border-border-default shadow-pasture-sm animate-pulse space-y-2 rounded-lg border p-4"
        >
          <div className="flex items-center gap-2">
            <div className="bg-surface-sunken h-3.5 w-3.5 rounded-full" />
            <div className="bg-surface-sunken h-3 w-16 rounded" />
            <div className="bg-surface-sunken h-3 w-24 rounded" />
          </div>
          <div className="bg-surface-sunken h-3 w-3/4 rounded" />
          <div className="bg-surface-sunken h-3 w-1/2 rounded" />
        </div>
      ))}
    </div>
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
      <div className="border-border-default bg-surface-raised space-y-4 rounded-xl border p-4">
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
        <header className="border-border-default mb-6 border-b pb-4">
          <p className="text-text-muted text-xs font-medium tracking-wide uppercase">
            {t('dreams.header.eyebrow', { count: nightCount })}
          </p>
          <h1 className="font-display text-display text-text-primary mt-1 font-medium">
            {t('dreams.header.statement')}
          </h1>
        </header>

        {/* Single-column reflection feed + right-side rail (DREAMS-02) */}
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
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
                <ul className="flex flex-col gap-3">
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
                </ul>
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

/**
 * Founder dashboard — Direction-A Pasture Home surface (THR-030 Leg B).
 *
 * Single useDashboardSummary() query powers the whole page. A wide MAIN
 * column carries the Waiting-on-you escalation queue + Recent-activity
 * feed; a narrower RIGHT RAIL stacks the secondary cards (Today heartbeat
 * + counters, Org pulse, This week's burn, top-token threads,
 * Updates-this-week). The "This week's burn" card sources its own /tokens
 * rollup (useTokensWeek), not DashboardSummaryResponse (THR-030 HOME-06).
 *
 * Design: a-dashboard.html reference from the Direction-A design bundle.
 * Pasture tokens (tokens.css) provide the full warm/green OKLCH palette
 * with Hanken Grotesk UI, Newsreader display serif, JetBrains Mono.
 *
 * Spec: docs/superpowers/specs/2026-05-30-dashboard-overhaul-design.md
 */
import { useState, type ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useDashboardSummary } from '@/hooks/dashboard';
import { useTokensToday, useTokensWeek } from '@/hooks/tokens';
import { Button } from '@/design-system/primitives/Button';
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap';
import { CrescentMoonBadge } from '@/design-system/patterns/CrescentMoonBadge';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { StatValue } from '@/design-system/patterns/StatValue';
import { TONE_CLASS, type Tone } from '@/design-system/patterns/semanticTone';
import type { ActivityVerdict } from '@/lib/api/types';
import { useOrgSlugOptional } from '@/lib/orgSlug';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';
import { formatAge, statusSummary } from './dashboardCopy';
import { Heartbeat } from './components/Heartbeat';
import { NarrativeParagraph } from './components/NarrativeParagraph';
import { OrgPulseTable } from './components/OrgPulseTable';
import { EscalationInboxRow } from './components/EscalationInboxRow';
import { TopTokenThreadsPanel } from './components/TopTokenThreadsPanel';

/** Whole seconds elapsed from `iso` to `now` (never negative). */
function secondsSince(iso: string, now: Date): number {
  return Math.max(0, Math.floor((now.getTime() - new Date(iso).getTime()) / 1000));
}

/**
 * Recent-activity outcome, rendered as a leading colored pill chip per the
 * a-dashboard reference (THR-099 Batch 3). Replaces the trailing grey mono
 * "· ok" text with the shared semantic colour vocabulary (semanticTone): our
 * data carries only ok/fail/warn — the design's richer done/merged/superseded
 * vocabulary needs fields we do not store, so we honestly colour what we have.
 */
const VERDICT_TONE: Record<ActivityVerdict, Tone> = {
  ok: 'positive',
  fail: 'danger',
  warn: 'attention',
};

/** Localized display label per verdict; the machine value keeps driving tone. */
const VERDICT_LABEL_KEY: Record<ActivityVerdict, MessageKey> = {
  ok: 'dashboard.verdict.ok',
  fail: 'dashboard.verdict.fail',
  warn: 'dashboard.verdict.warn',
};

function ActivityVerdictPill({ verdict }: { verdict: ActivityVerdict }): JSX.Element {
  const { t } = useTranslation();
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 font-medium ${TONE_CLASS[VERDICT_TONE[verdict]]}`}
    >
      {t(VERDICT_LABEL_KEY[verdict])}
    </span>
  );
}

interface PanelProps {
  title: string;
  meta?: string;
  /**
   * Header hierarchy (THR-030 HOME-07). 'eyebrow' (default) paints the small
   * uppercase tracked label the secondary right-rail cards use; 'title' paints
   * the Direction-A serif card title (var(--font-display)) the primary
   * main-column cards use. The a-dashboard reference mixes serif card titles
   * (Waiting on you / Recent activity) with small eyebrows (Today / Org pulse
   * / This week's burn) rather than one uniform all-caps utilitarian label.
   */
  variant?: 'eyebrow' | 'title';
  children: ReactNode;
}

/** Direction-A Pasture card — matches ds.css .card (bg-surface, rounded-lg 18px, soft shadow). */
function Panel({ title, meta, variant = 'eyebrow', children }: PanelProps): JSX.Element {
  return (
    <section className="border-border-default bg-surface shadow-pasture-sm rounded-lg border p-5">
      <header className="mb-4 flex items-baseline justify-between">
        {variant === 'title' ? (
          <h2 className="font-display text-h3 text-text-primary font-medium">
            {title}
          </h2>
        ) : (
          <h2 className="text-text-secondary text-xs font-semibold tracking-wider uppercase">
            {title}
          </h2>
        )}
        {meta ? (
          <span className="text-text-muted font-mono text-xs">{meta}</span>
        ) : null}
      </header>
      {children}
    </section>
  );
}

function useActiveSlug(): string | null {
  const { slug } = useParams<{ slug: string }>();
  const ctx = useOrgSlugOptional();
  return slug ?? ctx ?? null;
}

/** Local-midnight ISO of the given instant's calendar day. */
function startOfLocalDayIso(d: Date): string {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).toISOString();
}

export function DashboardPage(): JSX.Element {
  const { t, locale } = useTranslation();
  const queryClient = useQueryClient();
  const q = useDashboardSummary();
  const [expandedEscId, setExpandedEscId] = useState<string | null>(null);
  const slug = useActiveSlug();

  // Today-scoped REAL token total for the TODAY card (THR-030 HOME-04). The
  // day boundary is derived from the server clock (server_now), not the
  // browser, so the figure stays consistent with the rest of the page; local
  // midnight of that day is the `since` filter on the existing GET /tokens
  // route. Called unconditionally (rules-of-hooks); disabled until loaded.
  const tokensTodaySince = q.data
    ? startOfLocalDayIso(new Date(q.data.server_now))
    : undefined;
  const tokensTodayQ = useTokensToday({ since: tokensTodaySince });

  // This-week REAL token total for the "This week's burn" rail card (THR-030
  // HOME-06). Same honest /tokens rollup as the Tokens-today tile, only the
  // window differs: a rolling 7d back from the server clock (server_now), so
  // the figure stays consistent with the rest of the page and matches the
  // Spend page's same-window number the chevron links to. Disabled until
  // server_now is loaded.
  const tokensWeekSince = q.data
    ? new Date(new Date(q.data.server_now).getTime() - 7 * 86_400_000).toISOString()
    : undefined;
  const tokensWeekQ = useTokensWeek({ since: tokensWeekSince });
  // Honest display value: the real total only once resolved; the neutral
  // em-dash while pending/disabled/errored — never a fabricated 0. Rendered
  // via the overflow-safe StatValue (compact visible text, full precision in
  // the title) — same primitive the sibling Tokens-today tile rides, so the
  // exact figure is never lost on hover (THR-099 number-overflow).
  const weekBurnDisplay: ReactNode =
    tokensWeekQ.data !== undefined && !tokensWeekQ.isError
      ? <StatValue value={tokensWeekQ.data} align="inline" locale={locale} />
      : '—';

  if (q.isLoading) {
    return <p className="text-text-muted p-6 text-sm">{t('dashboard.loading')}</p>;
  }
  if (q.isError || !q.data) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-8 text-center">
        <p className="text-tier-red text-sm">{t('dashboard.error')}</p>
        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            queryClient.invalidateQueries({
              queryKey: ['dashboard-summary', slug],
            })
          }
        >
          {t('dashboard.retry')}
        </Button>
      </div>
    );
  }
  const s = q.data;
  const pendingReviewJobs = s.pending_review_jobs ?? [];
  const now = new Date(s.server_now);
  const nowHour = now.getUTCHours();

  // First-run empty state for a brand-new org with no activity. The waiting
  // signal must come from the same routable escalation list that feeds the
  // "Waiting on you" card, not the summary counter (THR-140 #573).
  if (
    s.org_age_days === 0 &&
    s.narrative_counts.completed_today === 0 &&
    s.narrative_counts.failed_today === 0 &&
    s.escalations.length === 0 &&
    pendingReviewJobs.length === 0
  ) {
    return (
      <div className="mx-auto max-w-2xl p-8">
        <EmptyState
          title={t('dashboard.firstRun.title')}
          body={t('dashboard.firstRun.body')}
        />
      </div>
    );
  }

  const pendingCount = s.escalations.length + pendingReviewJobs.length;

  return (
    <ContentWrap>
        {/* Header chrome — uppercase-tracked eyebrow ABOVE the serif greeting,
            matching the a-dashboard reference and the Jobs/Audit/Tasks header
            pattern already shipped (THR-099 PR3). Same live meta (date · org-age
            · agents-active), moved from a mono line UNDER the title to the shared
            eyebrow-over-serif chrome so Home reads consistently with the other
            surfaces. The greeting stays the data-derived status summary. */}
        <p className="text-text-muted mb-2 text-xs font-medium tracking-wide uppercase">
          {now.toLocaleDateString(locale, {
            weekday: 'long',
            month: 'long',
            day: 'numeric',
          })}
          {' · '}
          {t('dashboard.header.meta', {
            day: s.org_age_days,
            count: s.narrative_counts.agents_active_now,
          })}
          {s.generated_at && (
            <span className="text-text-muted/70 ml-1 font-normal normal-case tracking-normal">
              {'· '}
              {t('dashboard.header.updated', {
                age: formatAge(locale, secondsSince(s.generated_at, now)),
              })}
            </span>
          )}
          {!s.generated_at && (
            <span className="text-text-muted/50 ml-1 font-normal normal-case tracking-normal">
              {'· '}
              {t('dashboard.header.loadingSnapshot')}
            </span>
          )}
        </p>
        <h1 className="font-display text-display text-text-primary mb-8 font-medium">
          {statusSummary(locale, pendingCount)}
        </h1>

        {/* Direction-A a-dashboard layout: a wide MAIN column (Waiting-on-you
            queue + Recent-activity feed) beside a narrower RIGHT RAIL of
            secondary cards (Today / Org pulse / token cards). THR-030 HOME-03. */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          {/* MAIN COLUMN — Waiting-on-you queue on top, Recent-activity feed below */}
          <div
            className="space-y-4 lg:col-span-2"
            data-testid="dashboard-main"
            aria-label={t('dashboard.column.main')}
          >
            <Panel
              variant="title"
              title={
                pendingCount > 0
                  ? t('dashboard.waiting.titleCount', { count: pendingCount })
                  : t('dashboard.waiting.title')
              }
              meta={pendingCount > 0 ? t('dashboard.waiting.meta') : undefined}
            >
              {pendingCount === 0 ? (
                <EmptyState
                  title={t('dashboard.waiting.emptyTitle')}
                  body={t('dashboard.waiting.emptyBody')}
                />
              ) : (
                <div className="space-y-2">
                  {s.escalations.map((row) => (
                    <EscalationInboxRow
                      key={row.task_id}
                      row={row}
                      expanded={expandedEscId === row.task_id}
                      onExpand={() => setExpandedEscId(row.task_id)}
                      onCollapse={() => setExpandedEscId(null)}
                      slug={slug ?? ''}
                    />
                  ))}
                  {pendingReviewJobs.map((job) => (
                    <div
                      key={job.id}
                      className="border-border-subtle flex flex-wrap items-baseline gap-x-2 gap-y-1 rounded-md border p-3"
                    >
                      <span className="text-text-primary font-mono text-xs font-medium">
                        {job.agent_name}
                      </span>
                      <span className="text-text-muted font-mono text-xs">·</span>
                      <span className="text-id-task font-mono text-xs">{job.id}</span>
                      <span className="text-text-primary text-sm">{job.title}</span>
                      {slug ? (
                        <Link
                          to={`/orgs/${slug}/jobs/${job.id}`}
                          className="text-link-primary ml-auto text-sm hover:underline"
                        >
                          {t('dashboard.waiting.reviewJob')}
                        </Link>
                      ) : null}
                    </div>
                  ))}
                </div>
              )}
            </Panel>

            <Panel variant="title" title={t('dashboard.activity.title')}>
              {s.recent_activity.length === 0 ? (
                <p className="text-text-muted text-sm">{t('dashboard.activity.empty')}</p>
              ) : (
                <ul className="space-y-1 font-mono text-xs">
                  {s.recent_activity.map((r, i) => (
                    <li
                      key={`${r.timestamp}-${i}`}
                      className="flex items-baseline gap-2"
                    >
                      <span className="text-text-muted">
                        {t('dashboard.ago', {
                          age: formatAge(locale, secondsSince(r.timestamp, now)),
                        })}
                      </span>
                      {r.verdict && <ActivityVerdictPill verdict={r.verdict} />}
                      <span className="text-text-primary">{r.who}</span>
                      <span className="text-text-muted">
                        {r.event_kind.replace(/_/g, ' ')}
                      </span>
                      {r._thread_dream_id && (
                        <CrescentMoonBadge
                          className="h-3 w-3"
                          label={t('dashboard.dreamBadge')}
                        />
                      )}
                      {r.task_id && slug && (
                        <Link
                          to={
                            r.task_id.startsWith('THR-')
                              ? `/orgs/${slug}/threads/${r.task_id}`
                              : `/orgs/${slug}/tasks/${r.task_id}`
                          }
                          className="text-id-task ml-auto hover:underline"
                        >
                          {r.task_id}
                        </Link>
                      )}
                      {r.task_id && !slug && (
                        <span className="text-id-task ml-auto">{r.task_id}</span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>

          {/* RIGHT RAIL — Today / Org pulse / token cards */}
          <div
            className="space-y-4"
            data-testid="dashboard-rail"
            aria-label={t('dashboard.column.rail')}
          >
            <Panel title={t('dashboard.today.title')} meta={t('dashboard.today.meta')}>
              <Heartbeat data={s.heartbeat} nowIdx={nowHour} />
              <div className="mt-3">
                <NarrativeParagraph
                  counts={s.narrative_counts}
                  escalationCount={pendingCount}
                />
              </div>
              {/* Counter tiles — ds.css display-num / mono pattern */}
              <div className="border-border-default mt-5 grid grid-cols-5 gap-3 border-t pt-4">
                <div className="text-center">
                  <div className="font-display text-h1 text-text-primary font-medium tabular-nums">
                    {s.narrative_counts.completed_today}
                  </div>
                  <div className="text-text-muted text-overline mt-1">{t('dashboard.today.completed')}</div>
                </div>
                <div className="text-center">
                  <div
                    className={
                      s.narrative_counts.failed_today
                        ? 'font-display text-h1 text-tier-red font-medium tabular-nums'
                        : 'font-display text-h1 text-text-muted font-medium tabular-nums'
                    }
                  >
                    {s.narrative_counts.failed_today}
                  </div>
                  <div className="text-text-muted text-overline mt-1">{t('dashboard.today.failed')}</div>
                </div>
                <div className="text-center">
                  <div className="font-display text-h1 text-text-primary font-medium tabular-nums">
                    {s.narrative_counts.agents_active_now}
                  </div>
                  <div className="text-text-muted text-overline mt-1">{t('dashboard.today.active')}</div>
                </div>
                <div className="text-center">
                  <div className="font-display text-h1 text-text-primary font-medium tabular-nums">
                    +{s.narrative_counts.kb_added_today}
                  </div>
                  <div className="text-text-muted text-overline mt-1">{t('dashboard.today.kbEntries')}</div>
                </div>
                <div className="text-center">
                  {/* Tokens-today holds a compact token SUM (e.g. "346.1K")
                      which is wider than a raw count. It steps down to text-h2
                      (vs the sibling counts' text-h1) so the value fits its 1/5
                      column without colliding with the card edge (THR-099
                      number-overflow: overflow-11.24.21), and renders via the
                      overflow-safe StatValue so full precision stays in the
                      title. Honest tile: show the real summed total ONLY once
                      the /tokens query has succeeded with a defined figure;
                      while pending, disabled, or errored the value is unknown so
                      we show the dashboard's neutral em-dash placeholder rather
                      than a fabricated 0 (THR-030 HOME-04). */}
                  <div className="font-display text-h2 text-text-primary font-medium tabular-nums">
                    {tokensTodayQ.data !== undefined && !tokensTodayQ.isError ? (
                      <StatValue value={tokensTodayQ.data} align="inline" locale={locale} />
                    ) : (
                      '—'
                    )}
                  </div>
                  <div className="text-text-muted text-overline mt-1">{t('dashboard.today.tokensToday')}</div>
                </div>
              </div>
            </Panel>

            <Panel title={t('dashboard.pulse.title')} meta={t('dashboard.pulse.meta')}>
              <OrgPulseTable rows={s.org_pulse} />
            </Panel>

            {/* This week's burn — glance card deep-linking to Spend (THR-030
                HOME-06). The figure is the honest 7d token total from the same
                /tokens rollup the Tokens-today tile rides; tokens are the unit
                (dollar burn is deferred). While the query is pending/errored
                the value is unknown, so we paint the neutral em-dash, never a
                fabricated 0. The chevron drills into the Spend page's
                same-window number. */}
            <Panel title={t('dashboard.burn.title')} meta={t('dashboard.burn.meta')}>
              {slug ? (
                <Link
                  to={`/orgs/${slug}/spend`}
                  aria-label={t('dashboard.burn.link')}
                  className="group flex items-end justify-between"
                >
                  <div>
                    <div className="font-display text-h1 text-text-primary font-medium tabular-nums">
                      {weekBurnDisplay}
                    </div>
                    <div className="text-text-muted text-overline mt-1">{t('dashboard.burn.tokens')}</div>
                  </div>
                  <ChevronRight
                    size={20}
                    aria-hidden="true"
                    className="text-text-muted group-hover:text-text-primary shrink-0"
                  />
                </Link>
              ) : (
                <div>
                  <div className="font-display text-h1 text-text-primary font-medium tabular-nums">
                    {weekBurnDisplay}
                  </div>
                  <div className="text-text-muted text-overline mt-1">{t('dashboard.burn.tokens')}</div>
                </div>
              )}
            </Panel>

            {/* Self-contained cost-oversight card — fetches its own
                /tokens?group_by=thread data, not DashboardSummaryResponse. */}
            <TopTokenThreadsPanel />

            <Panel title={t('dashboard.updates.title')}>
              {s.updates_this_week.length === 0 ? (
                <p className="text-text-muted text-sm">{t('dashboard.updates.empty')}</p>
              ) : (
                <ul className="space-y-1 font-mono text-xs">
                  {s.updates_this_week.map((u, i) => (
                    <li
                      key={`${u.timestamp}-${i}`}
                      className="flex items-baseline gap-2"
                    >
                      <span
                        className={
                          u.marker === 'add'
                            ? 'text-tier-green'
                            : u.marker === 'warn'
                            ? 'text-tier-yellow'
                            : 'text-text-muted'
                        }
                      >
                        {u.marker === 'add'
                          ? '+'
                          : u.marker === 'warn'
                          ? '!'
                          : '·'}
                      </span>
                      <span className="text-text-primary">{u.text}</span>
                      <span className="text-text-muted">{u.meta}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>
        </div>
    </ContentWrap>
  );
}

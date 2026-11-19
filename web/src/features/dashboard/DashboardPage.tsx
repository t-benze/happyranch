import { IdentityName } from '@/shared/identities/IdentityName';
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
 * Spec: docs/specs/2026-05-30-dashboard-overhaul-design.md
 */
import { useState, type ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useDashboardSummary } from '@/hooks/dashboard';
import { useTokensToday, useTokensWeek } from '@/hooks/tokens';
import { Button } from '@/design-system/primitives/Button';
import { Card } from '@/design-system/primitives/Card';
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap';
import { CrescentMoonBadge } from '@/design-system/patterns/CrescentMoonBadge';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { List, Row, RowAction } from '@/design-system/patterns/List';
import { StatValue } from '@/design-system/patterns/StatValue';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import type { Tone } from '@/design-system/patterns/semanticTone';
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
  // THR-376 r2 A10: the shared StatusBadge (.tag 11/600, body font), not an inherited mono 12/500 pill.
  return <StatusBadge tone={VERDICT_TONE[verdict]} label={t(VERDICT_LABEL_KEY[verdict])} />;
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

type PanelHeaderProps = Omit<PanelProps, 'children'>;

function PanelHeader({ title, meta, variant = 'eyebrow' }: PanelHeaderProps): JSX.Element {
  return (
    // Wraps instead of squeezing: in the narrow lg rail (1024-1279px) a long
    // eyebrow + meta pair no longer fits one line (THR-376 D2).
    <header className="mb-4 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
      {variant === 'title' ? (
        // THR-376 r2 G3: card title Newsreader 18/500.
        <h2 className="font-display text-h2 text-text-primary font-medium">
          {title}
        </h2>
      ) : (
        // THR-376 r2 G4: Pasture `.eyebrow` recipe (11/600, .07em, ink-3).
        <h2 className="text-eyebrow text-text-muted uppercase">
          {title}
        </h2>
      )}
      {meta ? (
        <span className="text-text-muted font-mono text-xs whitespace-nowrap">{meta}</span>
      ) : null}
    </header>
  );
}

/** Direction-A Pasture card — the shared `Card` (ds.css .card, `.card-pad` 20px; THR-376 r2 G2). */
function Panel({ title, meta, variant = 'eyebrow', children }: PanelProps): JSX.Element {
  return (
    <Card as="section">
      <PanelHeader title={title} meta={meta} variant={variant} />
      {children}
    </Card>
  );
}

/**
 * One group inside the right-rail card (THR-376 r2 L1): a divider `Row` of the
 * shared `List`, padded like `.card-pad` (20px, G2), with an eyebrow header.
 */
function RailSection({ title, meta, children }: Omit<PanelProps, 'variant'>): JSX.Element {
  return (
    <Row padding="none" className="p-5">
      <section>
        <PanelHeader title={title} meta={meta} />
        {children}
      </section>
    </Row>
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
    return <p className="text-text-muted p-8 text-sm">{t('dashboard.loading')}</p>;
  }
  if (q.isError || !q.data) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-16 text-center">
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
      <div className="mx-auto max-w-2xl p-16">
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
        <h1 className="font-display text-display text-text-primary mb-5.5 font-medium">
          {statusSummary(locale, pendingCount)}
        </h1>

        {/* Direction-A a-dashboard layout: a wide MAIN column (Waiting-on-you
            queue + Recent-activity feed) beside a narrower RIGHT RAIL of
            secondary cards (Today / Org pulse / token cards). THR-030 HOME-03.
            THR-376 r2 G1: main column 1fr beside a fixed 340px rail, gap 22px. */}
        <div className="flex flex-col gap-5.5 lg:flex-row lg:items-start">
          {/* MAIN COLUMN — Waiting-on-you queue on top, Recent-activity feed below */}
          <div
            className="min-w-0 space-y-4 lg:flex-1"
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
                        <IdentityName canonicalId={job.agent_name} />
                      </span>
                      <span className="text-text-muted font-mono text-xs">·</span>
                      <IdBadge id={job.id} kind="job" />
                      <span className="text-text-primary text-sm">{job.title}</span>
                      {slug ? (
                        <Link
                          to={`/orgs/${slug}/jobs/${job.id}`}
                          className="text-accent-text ml-auto text-sm hover:underline"
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
                      className="flex flex-wrap items-baseline gap-x-2 gap-y-1"
                    >
                      {/* THR-376 r2 D6: every row starts with a pill in a
                          fixed-width slot, and the age has a fixed-width slot,
                          so agent names line up. Rows with no verdict (e.g.
                          session_start) get a neutral "—" pill (the dashboard's
                          "none" mark), not a made-up word. The trailing event
                          kind / ID wrap to a second line when the main column
                          is narrow (1100, mobile). */}
                      <span className="flex w-12 shrink-0">
                        {r.verdict ? (
                          <ActivityVerdictPill verdict={r.verdict} />
                        ) : (
                          <StatusBadge tone="neutral" label="—" />
                        )}
                      </span>
                      <span className="text-text-muted w-16 shrink-0 whitespace-nowrap">
                        {t('dashboard.ago', {
                          age: formatAge(locale, secondsSince(r.timestamp, now)),
                        })}
                      </span>
                      <span className="text-text-primary">{r.who}</span>
                      {/* Raw audit machine value — rendered byte-for-byte. */}
                      <span className="text-text-muted">{r.event_kind}</span>
                      {r._thread_dream_id && (
                        <CrescentMoonBadge
                          className="h-3 w-3"
                          label={t('dashboard.dreamBadge')}
                        />
                      )}
                      {r.task_id && (
                        <span className="ml-auto">
                          <IdBadge
                            id={r.task_id}
                            kind={r.task_id.startsWith('THR-') ? 'thread' : 'task'}
                            to={
                              slug
                                ? r.task_id.startsWith('THR-')
                                  ? `/orgs/${slug}/threads/${r.task_id}`
                                  : `/orgs/${slug}/tasks/${r.task_id}`
                                : undefined
                            }
                          />
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>

          {/* RIGHT RAIL — Today / Org pulse / token cards. THR-376 r2 L1: one
              card (the shared List) with a divider Row per group, not a stack
              of separate cards; fixed 340px wide from lg (G1). */}
          <div className="lg:w-85 lg:shrink-0" data-testid="dashboard-rail">
            <List aria-label={t('dashboard.column.rail')}>
              <RailSection title={t('dashboard.today.title')} meta={t('dashboard.today.meta')}>
                <Heartbeat data={s.heartbeat} nowIdx={nowHour} />
                <div className="mt-3">
                  <NarrativeParagraph
                    counts={s.narrative_counts}
                    escalationCount={pendingCount}
                  />
                </div>
                {/* Counter tiles — ds.css display-num / mono pattern. Five equal
                    columns collapsed to 32px at 1100 and 53px at 1440, so labels
                    collided or wrapped (THR-376 D4). Three columns fit every label
                    on one line, including in the fixed 340px rail (THR-376 r2
                    G1), so the old two-column narrow-rail step is gone.
                    THR-376 r2 G10: numbers 20px (tokens 15px); Completed in
                    accent-text. */}
                <div className="border-border-default mt-6 grid grid-cols-3 items-end gap-x-3 gap-y-4 border-t pt-4">
                  <div className="text-center">
                    <div className="font-display text-accent-text text-xl font-medium tabular-nums">
                      {s.narrative_counts.completed_today}
                    </div>
                    <div className="text-text-muted text-overline mt-1">{t('dashboard.today.completed')}</div>
                  </div>
                  <div className="text-center">
                    <div
                      className={
                        s.narrative_counts.failed_today
                          ? 'font-display text-tier-red text-xl font-medium tabular-nums'
                          : 'font-display text-text-muted text-xl font-medium tabular-nums'
                      }
                    >
                      {s.narrative_counts.failed_today}
                    </div>
                    <div className="text-text-muted text-overline mt-1">{t('dashboard.today.failed')}</div>
                  </div>
                  <div className="text-center">
                    <div className="font-display text-text-primary text-xl font-medium tabular-nums">
                      {s.narrative_counts.agents_active_now}
                    </div>
                    <div className="text-text-muted text-overline mt-1">{t('dashboard.today.active')}</div>
                  </div>
                  <div className="text-center">
                    <div className="font-display text-text-primary text-xl font-medium tabular-nums">
                      +{s.narrative_counts.kb_added_today}
                    </div>
                    <div className="text-text-muted text-overline mt-1">{t('dashboard.today.kbEntries')}</div>
                  </div>
                  <div className="text-center">
                    {/* Tokens-today holds a compact token SUM (e.g. "346.1K")
                        which is wider than a raw count. It steps down to 15px
                        (vs the sibling counts' 20px) so the value fits its 1/5
                        column without colliding with the card edge (THR-099
                        number-overflow: overflow-11.24.21), and renders via the
                        overflow-safe StatValue so full precision stays in the
                        title. Honest tile: show the real summed total ONLY once
                        the /tokens query has succeeded with a defined figure;
                        while pending, disabled, or errored the value is unknown so
                        we show the dashboard's neutral em-dash placeholder rather
                        than a fabricated 0 (THR-030 HOME-04). */}
                    <div className="font-display text-body-lg text-text-primary font-medium tabular-nums">
                      {tokensTodayQ.data !== undefined && !tokensTodayQ.isError ? (
                        <StatValue value={tokensTodayQ.data} align="inline" locale={locale} />
                      ) : (
                        '—'
                      )}
                    </div>
                    <div className="text-text-muted text-overline mt-1">{t('dashboard.today.tokensToday')}</div>
                  </div>
                </div>
              </RailSection>

              <RailSection title={t('dashboard.pulse.title')} meta={t('dashboard.pulse.meta')}>
                <OrgPulseTable rows={s.org_pulse} />
              </RailSection>

              {/* This week's burn — glance card deep-linking to Spend (THR-030
                  HOME-06). The figure is the honest 7d token total from the same
                  /tokens rollup the Tokens-today tile rides; tokens are the unit
                  (dollar burn is deferred). While the query is pending/errored
                  the value is unknown, so we paint the neutral em-dash, never a
                  fabricated 0. The chevron drills into the Spend page's
                  same-window number. THR-376 r2 G11: the whole group row is the
                  link (RowAction), not only the inner figure. */}
              {slug ? (
                <Row padding="none">
                  <RowAction asChild>
                    <Link
                      to={`/orgs/${slug}/spend`}
                      aria-label={t('dashboard.burn.link')}
                      className="group block p-5"
                    >
                      <PanelHeader title={t('dashboard.burn.title')} meta={t('dashboard.burn.meta')} />
                      <div className="flex items-end justify-between">
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
                      </div>
                    </Link>
                  </RowAction>
                </Row>
              ) : (
                <RailSection title={t('dashboard.burn.title')} meta={t('dashboard.burn.meta')}>
                  <div>
                    <div className="font-display text-h1 text-text-primary font-medium tabular-nums">
                      {weekBurnDisplay}
                    </div>
                    <div className="text-text-muted text-overline mt-1">{t('dashboard.burn.tokens')}</div>
                  </div>
                </RailSection>
              )}

              {/* Self-contained cost-oversight group — fetches its own
                  /tokens?group_by=thread data, not DashboardSummaryResponse. */}
              <Row padding="none" className="p-5">
                <TopTokenThreadsPanel />
              </Row>

              <RailSection title={t('dashboard.updates.title')}>
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
                        {/* THR-376 r2 G13: "KB: <title>" in ink, then a muted
                            age; one line (truncated, full text in the title) so
                            long slugs no longer wrap at 1100. KB rows carry only
                            the entry slug, so the slug stands in for the title. */}
                        {u.marker === 'add' ? (
                          <span
                            className="text-text-primary min-w-0 flex-1 truncate"
                            title={u.meta}
                          >
                            {t('dashboard.updates.kbEntry', { title: u.meta })}
                          </span>
                        ) : (
                          <span
                            className="min-w-0 flex-1 truncate"
                            title={`${u.text} ${u.meta}`}
                          >
                            <span className="text-text-primary">{u.text}</span>{' '}
                            <span className="text-text-muted">{u.meta}</span>
                          </span>
                        )}
                        <span className="text-text-muted shrink-0 whitespace-nowrap">
                          {formatAge(locale, secondsSince(u.timestamp, now))}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </RailSection>
            </List>
          </div>
        </div>
    </ContentWrap>
  );
}

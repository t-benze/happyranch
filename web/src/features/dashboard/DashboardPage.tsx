/**
 * Founder dashboard — Direction-A Pasture Home surface (THR-030 Leg B).
 *
 * Single useDashboardSummary() query powers the whole page. A wide MAIN
 * column carries the Waiting-on-you escalation queue + Recent-activity
 * feed; a narrower RIGHT RAIL stacks the secondary cards (Today heartbeat
 * + counters, Org pulse, top-token threads, Updates-this-week).
 *
 * Design: a-dashboard.html reference from the Direction-A design bundle.
 * Pasture tokens (tokens.css) provide the full warm/green OKLCH palette
 * with Hanken Grotesk UI, Newsreader display serif, JetBrains Mono.
 *
 * Spec: docs/superpowers/specs/2026-05-30-dashboard-overhaul-design.md
 */
import { useState, type ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useDashboardSummary } from '@/hooks/dashboard';
import { Button } from '@/design-system/primitives/Button';
import { CrescentMoonBadge } from '@/design-system/patterns/CrescentMoonBadge';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useOrgSlugOptional } from '@/lib/orgSlug';
import { Heartbeat } from './components/Heartbeat';
import { NarrativeParagraph } from './components/NarrativeParagraph';
import { OrgPulseTable } from './components/OrgPulseTable';
import { EscalationInboxRow } from './components/EscalationInboxRow';
import { TopTokenThreadsPanel } from './components/TopTokenThreadsPanel';

/**
 * Status-summary copy for the serif greeting heading (THR-030 HOME-02).
 * Derived only from the live waiting count — the same escalation queue
 * length the "Waiting on you · N" card surfaces — never a hand-authored
 * narrative.
 */
function statusSummary(pendingCount: number): string {
  if (pendingCount === 0) return "You're all caught up, founder";
  if (pendingCount === 1) return '1 thing needs you, founder';
  return `${pendingCount} things need you, founder`;
}

function relativeAge(iso: string, now: Date): string {
  const seconds = Math.max(
    0,
    Math.floor((now.getTime() - new Date(iso).getTime()) / 1000),
  );
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

interface PanelProps {
  title: string;
  meta?: string;
  children: ReactNode;
}

/** Direction-A Pasture card — matches ds.css .card (bg-surface, rounded-lg 18px, soft shadow). */
function Panel({ title, meta, children }: PanelProps): JSX.Element {
  return (
    <section className="border-border-default bg-surface shadow-pasture-sm rounded-lg border p-5">
      <header className="mb-4 flex items-baseline justify-between">
        <h2 className="text-text-secondary text-xs font-semibold tracking-wider uppercase">
          {title}
        </h2>
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

export function DashboardPage(): JSX.Element {
  const queryClient = useQueryClient();
  const q = useDashboardSummary();
  const [expandedEscId, setExpandedEscId] = useState<string | null>(null);
  const slug = useActiveSlug();

  if (q.isLoading) {
    return <p className="text-text-muted p-6 text-sm">Loading dashboard…</p>;
  }
  if (q.isError || !q.data) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-8 text-center">
        <p className="text-tier-red text-sm">Failed to load dashboard.</p>
        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            queryClient.invalidateQueries({
              queryKey: ['dashboard-summary', slug],
            })
          }
        >
          Retry
        </Button>
      </div>
    );
  }
  const s = q.data;
  const now = new Date(s.server_now);
  const nowHour = now.getUTCHours();

  // First-run empty state for a brand-new org with no activity.
  if (
    s.org_age_days === 0 &&
    s.narrative_counts.completed_today === 0 &&
    s.narrative_counts.failed_today === 0 &&
    s.narrative_counts.escalated_open === 0
  ) {
    return (
      <div className="mx-auto max-w-2xl p-8">
        <EmptyState
          title="Start your first brief"
          body="This is your founder dashboard. It will show today's activity, what's waiting on you, and per-team health once your agents run their first task."
        />
      </div>
    );
  }

  const pendingCount = s.escalations.length;

  return (
    <div className="bg-surface-canvas h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl p-6">
        {/* Greeting heading — Direction-A serif (var(--font-display)), ds.css
            .h1 parity; copy is a data-derived status summary (THR-030 HOME-02). */}
        <h1 className="font-display text-display text-text-primary mb-1 font-medium">
          {statusSummary(pendingCount)}
        </h1>
        <div className="text-text-muted mb-8 flex items-baseline gap-3 font-mono text-xs">
          <span className="text-text-primary font-medium">
            {now.toLocaleDateString(undefined, {
              weekday: 'short',
              month: 'short',
              day: 'numeric',
            })}
          </span>
          <span>·</span>
          <span>Day {s.org_age_days}</span>
          <span className="grow" />
          <span>{s.narrative_counts.agents_active_now} agents active</span>
          <span>·</span>
          <span>
            spend · ${s.narrative_counts.spend_today_usd.toFixed(2)} today
          </span>
        </div>

        {/* Direction-A a-dashboard layout: a wide MAIN column (Waiting-on-you
            queue + Recent-activity feed) beside a narrower RIGHT RAIL of
            secondary cards (Today / Org pulse / token cards). THR-030 HOME-03. */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          {/* MAIN COLUMN — Waiting-on-you queue on top, Recent-activity feed below */}
          <div
            className="space-y-4 lg:col-span-2"
            data-testid="dashboard-main"
            aria-label="Main column"
          >
            <Panel
              title={
                pendingCount > 0
                  ? `Waiting on you · ${pendingCount}`
                  : 'Waiting on you'
              }
              meta={pendingCount > 0 ? 'esc to close · ⌘↵ to send' : undefined}
            >
              {pendingCount === 0 ? (
                <EmptyState title="All clear" body="No escalations waiting." />
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
                </div>
              )}
            </Panel>

            <Panel title="Recent activity">
              {s.recent_activity.length === 0 ? (
                <p className="text-text-muted text-sm">No recent activity.</p>
              ) : (
                <ul className="space-y-1 font-mono text-xs">
                  {s.recent_activity.map((r, i) => (
                    <li
                      key={`${r.timestamp}-${i}`}
                      className="flex items-baseline gap-2"
                    >
                      <span className="text-text-muted">
                        {relativeAge(r.timestamp, now)} ago
                      </span>
                      <span className="text-text-primary">{r.who}</span>
                      <span className="text-text-muted">
                        {r.event_kind.replace(/_/g, ' ')}
                      </span>
                      {r.verdict === 'ok' && (
                        <span className="text-tier-green">· ok</span>
                      )}
                      {r.verdict === 'fail' && (
                        <span className="text-tier-red">· fail</span>
                      )}
                      {r._thread_dream_id && (
                        <CrescentMoonBadge className="h-3 w-3" />
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
            aria-label="Right rail"
          >
            <Panel title="Today" meta="last 24h">
              <Heartbeat data={s.heartbeat} nowIdx={nowHour} />
              <div className="mt-3">
                <NarrativeParagraph counts={s.narrative_counts} />
              </div>
              {/* Counter tiles — ds.css display-num / mono pattern */}
              <div className="border-border-default mt-5 grid grid-cols-5 gap-3 border-t pt-4">
                <div className="text-center">
                  <div className="font-display text-h1 text-tier-green font-medium tabular-nums">
                    {s.narrative_counts.completed_today}
                  </div>
                  <div className="text-text-muted text-overline mt-1">Completed</div>
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
                  <div className="text-text-muted text-overline mt-1">Failed</div>
                </div>
                <div className="text-center">
                  <div className="font-display text-h1 text-tier-green font-medium tabular-nums">
                    {s.narrative_counts.agents_active_now}
                  </div>
                  <div className="text-text-muted text-overline mt-1">Active</div>
                </div>
                <div className="text-center">
                  <div className="font-display text-h1 text-text-primary font-medium tabular-nums">
                    +{s.narrative_counts.kb_added_today}
                  </div>
                  <div className="text-text-muted text-overline mt-1">KB entries</div>
                </div>
                <div className="text-center">
                  <div className="text-h2 text-text-primary font-mono font-medium tabular-nums">
                    ${s.narrative_counts.spend_today_usd.toFixed(2)}
                  </div>
                  <div className="text-text-muted text-overline mt-1">Spend today</div>
                </div>
              </div>
            </Panel>

            <Panel title="Org pulse · last 7d" meta="acceptance %">
              <OrgPulseTable rows={s.org_pulse} />
            </Panel>

            {/* Self-contained cost-oversight card — fetches its own
                /tokens?group_by=thread data, not DashboardSummaryResponse. */}
            <TopTokenThreadsPanel />

            <Panel title="Updates this week">
              {s.updates_this_week.length === 0 ? (
                <p className="text-text-muted text-sm">No updates yet.</p>
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
      </div>
    </div>
  );
}

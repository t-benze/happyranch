/**
 * Founder dashboard.
 *
 * Single useDashboardSummary() query powers the whole page. Left column
 * is read-only (heartbeat + narrative + counters + activity), right
 * column is interactive (escalation inbox + updates feed).
 *
 * Spec: docs/superpowers/specs/2026-05-30-dashboard-overhaul-design.md
 */
import { useState, type ReactNode } from 'react';
import { useDashboardSummary } from '@/hooks/dashboard';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { Heartbeat } from './components/Heartbeat';
import { NarrativeParagraph } from './components/NarrativeParagraph';
import { OrgPulseTable } from './components/OrgPulseTable';
import { EscalationInboxRow } from './components/EscalationInboxRow';

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

function Panel({ title, meta, children }: PanelProps): JSX.Element {
  return (
    <section className="border-border-subtle bg-surface-sunken rounded-md border p-4">
      <header className="mb-3 flex items-baseline justify-between">
        <h2 className="text-text-muted text-xs font-medium tracking-wider uppercase">
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

export function DashboardPage(): JSX.Element {
  const q = useDashboardSummary();
  const [expandedEscId, setExpandedEscId] = useState<string | null>(null);

  if (q.isLoading) {
    return <p className="text-text-muted p-6 text-sm">Loading dashboard…</p>;
  }
  if (q.isError || !q.data) {
    return (
      <p className="text-feedback-danger p-6 text-sm">
        Failed to load dashboard. Try refreshing.
      </p>
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
      <div className="mx-auto max-w-7xl p-6">
        <div className="text-text-muted mb-4 flex items-baseline gap-3 font-mono text-xs">
          <span className="text-text-primary font-medium">
            {now.toLocaleDateString(undefined, {
              weekday: 'short',
              month: 'short',
              day: 'numeric',
            })}
          </span>
          <span>·</span>
          <span>Day {s.org_age_days} of org</span>
          <span className="grow" />
          <span>{s.narrative_counts.agents_active_now} agents active now</span>
          <span>·</span>
          <span>spend · ${s.narrative_counts.spend_today_usd.toFixed(2)} today</span>
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {/* LEFT COLUMN */}
          <div className="space-y-4">
            <Panel title="Today" meta="last 24h">
              <Heartbeat data={s.heartbeat} nowIdx={nowHour} />
              <div className="mt-3">
                <NarrativeParagraph counts={s.narrative_counts} />
              </div>
              <div className="border-border-subtle mt-4 flex justify-between border-t pt-3 font-mono text-xs">
                <div>
                  <div className="text-tier-green font-medium">
                    {s.narrative_counts.completed_today}
                  </div>
                  <div className="text-text-muted">completed</div>
                </div>
                <div>
                  <div
                    className={
                      s.narrative_counts.failed_today
                        ? 'text-tier-red font-medium'
                        : 'text-text-muted'
                    }
                  >
                    {s.narrative_counts.failed_today}
                  </div>
                  <div className="text-text-muted">failed</div>
                </div>
                <div>
                  <div
                    className={
                      s.narrative_counts.escalated_open
                        ? 'text-tier-yellow font-medium'
                        : 'text-text-muted'
                    }
                  >
                    {s.narrative_counts.escalated_open}
                  </div>
                  <div className="text-text-muted">escalated</div>
                </div>
                <div>
                  <div className="text-text-primary font-medium">
                    +{s.narrative_counts.kb_added_today}
                  </div>
                  <div className="text-text-muted">kb entries</div>
                </div>
                <div>
                  <div className="text-text-primary font-medium">
                    ${s.narrative_counts.spend_today_usd.toFixed(2)}
                  </div>
                  <div className="text-text-muted">spend</div>
                </div>
              </div>
            </Panel>

            <Panel title="Org pulse · last 7 days" meta="acceptance">
              <OrgPulseTable rows={s.org_pulse} />
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
                      {r.task_id && (
                        <span className="text-text-muted ml-auto">
                          {r.task_id}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>

          {/* RIGHT COLUMN */}
          <div className="space-y-4">
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
                    />
                  ))}
                </div>
              )}
            </Panel>

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

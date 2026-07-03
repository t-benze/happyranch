/**
 * FanoutBand — compact fan-out status band shown near the Task detail header
 * for the three fan-out lifecycle states (TASK-1717 polish, design target
 * TASK-1696). Presentation-only; every value is DERIVED and honesty-degraded
 * per the Step 0 reconciliation (no fabricated locales, tokens, executor
 * values, artifact links, or merge summaries).
 *
 *  - pending : awaiting founder approval to spawn N subtasks. Shows planned
 *              child agent/prompt snippets (from children_details) and the
 *              review_required approval job link. No subtasks exist yet.
 *  - running : N subtasks spawned; compact progress counts from recall.
 *  - joined  : parent resumed after all children terminal; terminal counts.
 *
 * Regular (non-fan-out) tasks never render this band — the caller only mounts
 * it when real fan-out evidence exists.
 */
import { Link } from 'react-router-dom';
import { AgentChip } from '@/design-system/patterns/AgentChip';
import type { ChildStatusCounts, FanoutPlannedChild } from './fanout';
import { progressSummary, snippet } from './fanout';

export type FanoutMode = 'pending' | 'running' | 'joined';

interface FanoutBandProps {
  mode: FanoutMode;
  /** Planned/known fan-out width. Null when not recorded on the payload. */
  width: number | null;
  /** Child status counts (running/joined). Null for pending (no children). */
  counts: ChildStatusCounts | null;
  /** Planned children for pending, from active_fanout.children_details. */
  plannedChildren: FanoutPlannedChild[];
  /** review_required approval job id for pending, when available. */
  approvalJobId: string | null;
  slug: string | undefined;
}

/** Fan-out glyph — a small branch icon echoing the design without new assets. */
function BranchIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <circle cx="6" cy="6" r="2.2" />
      <circle cx="18" cy="6" r="2.2" />
      <circle cx="18" cy="18" r="2.2" />
      <path d="M6 8.2v3.3a3 3 0 0 0 3 3h6.8" />
      <path d="M6 8.2V6" opacity="0" />
      <path d="M15.8 6H8.2" />
    </svg>
  );
}

/** Segmented progress bar for the running state. Widths are proportional to
 *  the counts; each segment is a design-token status colour. */
function ProgressBar({ counts }: { counts: ChildStatusCounts }): JSX.Element {
  const total = Math.max(counts.total, 1);
  const seg = (n: number, cls: string, key: string) =>
    n > 0 ? (
      <span
        key={key}
        className={cls}
        // eslint-disable-next-line react/forbid-dom-props -- proportional segment width is a runtime ratio of live child counts; no static utility class can express a data-driven percentage
        style={{ width: `${(n / total) * 100}%` }}
        aria-hidden
      />
    ) : null;
  return (
    <div className="bg-border-subtle mt-2 flex h-1.5 w-full overflow-hidden rounded-full">
      {seg(counts.completed, 'bg-status-open', 'done')}
      {seg(counts.running, 'bg-accent-default', 'run')}
      {seg(counts.failed, 'bg-status-abandoned', 'fail')}
      {seg(counts.queued, 'bg-border-strong', 'queue')}
    </div>
  );
}

function headline(
  mode: FanoutMode,
  width: number | null,
  counts: ChildStatusCounts | null,
): string {
  const n = width ?? counts?.total ?? 0;
  switch (mode) {
    case 'pending':
      return `Awaiting approval to spawn ${n} subtask${n === 1 ? '' : 's'}`;
    case 'running':
      return `Running fan-out — ${counts?.terminal ?? 0} of ${n} done`;
    case 'joined':
      return `Fan-out joined — ${counts?.completed ?? 0} of ${n} succeeded`;
  }
}

function toneClasses(mode: FanoutMode): { title: string; icon: string } {
  switch (mode) {
    case 'pending':
      return { title: 'text-status-archiving', icon: 'text-status-archiving' };
    case 'running':
      return { title: 'text-accent-default', icon: 'text-accent-default' };
    case 'joined':
      return { title: 'text-status-open', icon: 'text-status-open' };
  }
}

export function FanoutBand({
  mode,
  width,
  counts,
  plannedChildren,
  approvalJobId,
  slug,
}: FanoutBandProps): JSX.Element {
  const tone = toneClasses(mode);
  const n = width ?? counts?.total ?? 0;

  return (
    <section
      aria-label="Fan-out status"
      className="border-border-default bg-surface-raised mt-4 rounded-xl border p-4"
    >
      <div className="flex items-start gap-3">
        <span className={`mt-0.5 shrink-0 ${tone.icon}`}>
          <BranchIcon />
        </span>
        <div className="min-w-0 flex-1">
          <p className={`text-sm font-semibold ${tone.title}`}>
            {headline(mode, width, counts)}
          </p>

          {mode === 'pending' && (
            <p className="text-text-muted mt-1 text-xs">
              Founder approval required before spawning. No subtasks exist yet.
              {approvalJobId && (
                <>
                  {' '}
                  Approval gate ·{' '}
                  {slug ? (
                    <Link
                      to={`/orgs/${slug}/jobs/${approvalJobId}`}
                      className="text-accent-default font-mono hover:underline"
                    >
                      {approvalJobId}
                    </Link>
                  ) : (
                    <span className="font-mono">{approvalJobId}</span>
                  )}
                </>
              )}
            </p>
          )}

          {mode === 'running' && (
            <>
              <p className="text-text-muted mt-1 text-xs">
                Parent resumes automatically once every subtask reaches a
                terminal state.
              </p>
              {counts && (
                <>
                  <ProgressBar counts={counts} />
                  <p className="text-text-secondary mt-1.5 font-mono text-xs tabular-nums">
                    {progressSummary(counts)}
                  </p>
                </>
              )}
            </>
          )}

          {mode === 'joined' && counts && (
            <p className="text-text-muted mt-1 text-xs">
              Parent resumed and completed once all subtasks reached a terminal
              state.
              {counts.failed > 0 && (
                <>
                  {' '}
                  {counts.failed} subtask{counts.failed === 1 ? '' : 's'} did not
                  succeed.
                </>
              )}
            </p>
          )}
        </div>
      </div>

      {/* Running/joined: backed metadata only — planned width and the constant
          Phase-1 join mode (all children terminal before join). No fabricated
          executor/token/artifact fields. */}
      {(mode === 'running' || mode === 'joined') && n > 0 && (
        <div className="border-border-subtle text-text-muted mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t pt-2 text-xs">
          <span>
            Width <span className="text-text-secondary tabular-nums">{n}</span>
          </span>
          <span>
            Join <span className="text-text-secondary font-mono">all-terminal</span>
          </span>
        </div>
      )}

      {/* Pending: planned children preview (children_details), agent + prompt
          snippet only — never fabricated locale/domain labels. */}
      {mode === 'pending' && (
        <div className="border-border-subtle mt-3 border-t pt-3">
          <h4 className="text-text-secondary text-xs font-semibold tracking-wider uppercase">
            Planned fan-out
            <span className="text-text-muted ml-2 font-normal normal-case">
              {n} subtask{n === 1 ? '' : 's'} created on approval
            </span>
          </h4>
          {plannedChildren.length > 0 ? (
            <ul className="mt-2 space-y-1.5">
              {plannedChildren.map((c, i) => {
                const prompt = snippet(c.prompt, 120);
                return (
                  <li
                    key={i}
                    className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-sm"
                  >
                    {c.agent && <AgentChip name={c.agent} role="worker" />}
                    {prompt && (
                      <span className="text-text-secondary min-w-0 text-xs">
                        {prompt}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-text-muted mt-1 text-xs">
              Each becomes a sibling execution subtask once the fan-out is
              approved. The approval itself is a review job — the gate, not a
              child.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
